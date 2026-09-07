"""Tests for the orchestration service that pulls search-query-details data
from Ozon Seller API's POST /v1/analytics/product-queries/details (see
app.services.search_query_details_sync_service): SKU batching, upsert-by-
period history (no overwrite across different date ranges), field mapping
onto SearchQueryStatistic, and one batch's failure not aborting the others.
Uses a fake, duck-typed OzonSellerClient — the client's own request-building
behavior is already covered by test_ozon_seller_client.py."""
from datetime import date
from types import SimpleNamespace

from app.models.product import Product
from app.models.search_query_statistic import SearchQueryStatistic
from app.services.ozon.exceptions import OzonAPIError
from app.services.search_query_details_sync_service import sync_search_query_details

_SAMPLE_ITEM = {
    "sku": 2953864771,
    "currency": "RUB",
    "gmv": 71538.84,
    "order_count": 20,
    "position": 82,
    "query": "обувница",
    "view_conversion": 3.03,
    "query_index": 1,
    "unique_search_users": 189229,
    "unique_view_users": 24834,
}


class FakeOzonSellerClient:
    def __init__(self, responses: list[dict] | None = None, offer_id_lookup_responses: list[list[dict]] | None = None):
        self.calls: list[dict] = []
        self._responses = responses or []
        # Each entry is a list of {"offer_id": ..., "sku": ...} dicts, one
        # entry consumed per get_products_info_by_offer_id() call. Defaults
        # to an empty match (offer_id not found) if not configured, mirroring
        # a lookup that fails to resolve anything.
        self.offer_id_lookup_calls: list[list[str]] = []
        self._offer_id_lookup_responses = offer_id_lookup_responses or []

    def get_product_query_details(self, *, date_from, date_to, skus=None, limit_by_sku=15, page_size=100):
        self.calls.append(
            {"date_from": date_from, "date_to": date_to, "skus": list(skus or []), "limit_by_sku": limit_by_sku, "page_size": page_size}
        )
        if self._responses:
            return self._responses.pop(0)
        return {"items": [], "total": 0, "page_count": 1}

    def get_products_info_by_offer_id(self, offer_ids):
        self.offer_id_lookup_calls.append(list(offer_ids))
        payload = self._offer_id_lookup_responses.pop(0) if self._offer_id_lookup_responses else []
        return SimpleNamespace(items=[SimpleNamespace(offer_id=p["offer_id"], sku=p.get("sku")) for p in payload])


class ErrorOnceThenOkClient(FakeOzonSellerClient):
    def __init__(self, responses):
        super().__init__(responses)
        self._first = True

    def get_product_query_details(self, **kwargs):
        if self._first:
            self._first = False
            raise OzonAPIError("Ozon вернул ошибку сервера 500")
        return super().get_product_query_details(**kwargs)


_PERIOD_REJECTED_ERROR_TEXT = (
    'Ozon вернул ошибку 400: {"code":3,"message":"ProductQueriesDetails error: '
    "service.ProductQueriesDetails.getPremiumAnalyticsPeriod rpc error: code = "
    'InvalidArgument desc = There is no data for the specified period"}'
)


class PeriodLimitedClient:
    """Simulates Ozon's real (confirmed live) rejection of a date range it
    considers too long/too old: any request whose (date_to - date_from) span
    exceeds max_accepted_days raises the exact error text seen live; a span
    at or under it succeeds with `response`."""

    def __init__(self, max_accepted_days: int, response: dict):
        self.max_accepted_days = max_accepted_days
        self.response = response
        self.calls: list[dict] = []

    def get_product_query_details(self, *, date_from, date_to, skus=None, limit_by_sku=15, page_size=100):
        from datetime import datetime as _dt

        d_from = _dt.fromisoformat(date_from.replace("Z", "+00:00")).date()
        d_to = _dt.fromisoformat(date_to.replace("Z", "+00:00")).date()
        span = (d_to - d_from).days + 1
        self.calls.append({"date_from": date_from, "date_to": date_to, "span": span, "skus": list(skus or [])})
        if span > self.max_accepted_days:
            raise OzonAPIError(_PERIOD_REJECTED_ERROR_TEXT)
        return self.response


def _make_product(db_session, store_id: str, sku: str, *, name: str | None = None, offer_id: str | None = None):
    product = Product(store_id=store_id, ozon_sku=sku, name=name or f"Товар {sku}", offer_id=offer_id)
    db_session.add(product)
    db_session.flush()
    return product


def test_default_date_to_lags_behind_today_not_equal_to_it(db_session, two_stores_with_users, monkeypatch):
    """Regression test: a real run that defaulted date_to to literal "today"
    failed on every batch with Ozon's "There is no data for the specified
    period" — the confirmed working curl test used a date_to 2 days before
    the day it was run. The default must lag behind today by
    SEARCH_QUERY_STATS_DATA_LAG_DAYS, never equal it."""
    import app.services.search_query_details_sync_service as svc

    class _FixedDatetime(svc.datetime):
        @classmethod
        def now(cls, tz=None):
            return svc.datetime(2026, 9, 6, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(svc, "datetime", _FixedDatetime)

    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")
    client = FakeOzonSellerClient([{"items": [], "total": 0, "page_count": 1}])

    sync_search_query_details(db_session, store_id=d["store_a"].id, client=client)

    assert len(client.calls) == 1
    assert client.calls[0]["date_to"] == "2026-09-04T23:59:59Z"
    assert "2026-09-06" not in client.calls[0]["date_to"]


def test_sku_zero_is_filtered_out_and_reported(db_session, two_stores_with_users):
    """Regression test: Ozon's own product-info API can hand back sku=0 as a
    "no SKU assigned yet" sentinel, which used to end up stored as
    Product.ozon_sku="0" and then sent straight to Ozon, which rejected the
    whole batch with "Skus[N]: value must be greater than 0". A sku=0 (or
    otherwise non-positive) product must be filtered out before batching,
    and the skip must be visible in outcome.errors rather than silent — this
    covers the case where the offer_id fallback (see the tests below) also
    fails to resolve a real sku, so the product is still genuinely invalid."""
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")
    _make_product(db_session, d["store_a"].id, "0", name="Необувница", offer_id="art-42")

    client = FakeOzonSellerClient([{"items": [_SAMPLE_ITEM], "total": 1, "page_count": 1}])
    outcome = sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4)
    )

    assert client.offer_id_lookup_calls == [["art-42"]]  # the fallback was attempted...
    assert len(client.calls) == 1
    assert client.calls[0]["skus"] == ["2953864771"]  # ...but only the already-valid sku made it into the request
    assert outcome.created == 1
    # The diagnostic must name the specific product (not just a count) so a
    # seller can actually go check/fix it — a bare "1 товар пропущен" gave
    # no way to tell which product was the culprit.
    assert any("Необувница" in e and "art-42" in e for e in outcome.errors)


def test_zero_sku_product_is_resolved_via_offer_id_before_sync(db_session, two_stores_with_users):
    """Same real-world bug as app.api.routes.sync's sync_ozon_products fix:
    a product ("мус/вед/бел1/3", real sku 5716615794) can sit in the local
    catalog with ozon_sku="0" even though Ozon has since assigned it a real
    SKU. This module reads Product straight from the DB rather than calling
    Ozon's product list itself, so without this fallback such a row would be
    skipped by "Позиции в поиске" forever, even after being fixed by a fresh
    /sync/ozon-products run — the fix must live here too."""
    d = two_stores_with_users
    product = _make_product(
        db_session, d["store_a"].id, "0",
        name="Мусорное ведро для кухни и туалета с крышкой 11 л", offer_id="мус/вед/бел1/3",
    )

    resolved_item = {**_SAMPLE_ITEM, "sku": 5716615794}
    client = FakeOzonSellerClient(
        responses=[{"items": [resolved_item], "total": 1, "page_count": 1}],
        offer_id_lookup_responses=[[{"offer_id": "мус/вед/бел1/3", "sku": 5716615794}]],
    )

    outcome = sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4)
    )

    assert client.offer_id_lookup_calls == [["мус/вед/бел1/3"]]
    assert client.calls[0]["skus"] == ["5716615794"]  # the resolved sku, not "0"
    assert outcome.created == 1
    assert outcome.errors == []  # no longer skipped

    db_session.refresh(product)
    assert product.ozon_sku == "5716615794"  # corrected in place, benefiting the wider catalog too


def test_zero_sku_resolution_merges_conflicting_existing_product(db_session, two_stores_with_users):
    """Regression test for a real production crash: a stale ozon_sku="0"
    placeholder product and a SEPARATE row that already holds the resolved
    real sku can both exist for the same product — the historic sku=0
    duplicate-row bug (see app.services.product_merge's module docstring).
    Writing the resolved sku straight onto the placeholder would collide
    with the other row under uq_product_store_sku (confirmed live for
    offer_id "мус/вед/бел1/3", sku 5716615794) — this must merge the two
    rows (including reassigning the placeholder's own history) instead of
    crashing."""
    d = two_stores_with_users
    offer_id = "мус/вед/бел1/3"
    real_sku = 5716615794

    placeholder = _make_product(db_session, d["store_a"].id, "0", name="Плейсхолдер", offer_id=offer_id)
    real_row = _make_product(
        db_session, d["store_a"].id, str(real_sku),
        name="Мусорное ведро для кухни и туалета с крышкой 11 л",
    )
    # Attached to the LOSING row (invalid sku always loses — see
    # product_merge.pick_survivor) to prove the merge reassigns it rather
    # than discarding it.
    old_stat = SearchQueryStatistic(
        store_id=d["store_a"].id, ozon_sku="старый-ключ", query_text="старый запрос",
        period_start=date(2026, 7, 1), period_end=date(2026, 7, 31),
        source="manual_upload", product_id=placeholder.id,
    )
    db_session.add(old_stat)
    db_session.commit()

    resolved_item = {**_SAMPLE_ITEM, "sku": real_sku}
    client = FakeOzonSellerClient(
        responses=[{"items": [resolved_item], "total": 1, "page_count": 1}],
        offer_id_lookup_responses=[[{"offer_id": offer_id, "sku": real_sku}]],
    )

    outcome = sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4)
    )

    assert outcome.errors == []
    assert client.calls[0]["skus"] == [str(real_sku)]  # deduplicated, not sent twice

    db_session.expire_all()
    assert db_session.query(Product).filter(Product.store_id == d["store_a"].id).count() == 1
    assert db_session.get(SearchQueryStatistic, old_stat.id).product_id == real_row.id


def test_zero_sku_product_without_offer_id_is_not_retried(db_session, two_stores_with_users):
    """No offer_id means nothing to retry with — must be reported immediately,
    without attempting a lookup call."""
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")
    _make_product(db_session, d["store_a"].id, "0", name="Без артикула")

    client = FakeOzonSellerClient([{"items": [_SAMPLE_ITEM], "total": 1, "page_count": 1}])
    outcome = sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4)
    )

    assert client.offer_id_lookup_calls == []
    assert any("Без артикула" in e for e in outcome.errors)


def test_no_products_reports_a_clear_error(db_session, two_stores_with_users):
    d = two_stores_with_users
    client = FakeOzonSellerClient()

    outcome = sync_search_query_details(db_session, store_id=d["store_a"].id, client=client)

    assert outcome.created == 0
    assert len(outcome.errors) == 1
    assert "товар" in outcome.errors[0].lower()


def test_sends_full_iso_timestamps_and_both_limit_fields(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")

    client = FakeOzonSellerClient([{"items": [_SAMPLE_ITEM], "total": 1, "page_count": 1}])
    sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client,
        date_from=date(2026, 8, 8), date_to=date(2026, 9, 4),
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["date_from"] == "2026-08-08T00:00:00Z"
    assert call["date_to"] == "2026-09-04T23:59:59Z"
    assert call["skus"] == ["2953864771"]
    assert call["limit_by_sku"] > 0
    assert call["page_size"] > 0


def test_creates_row_with_mapped_fields(db_session, two_stores_with_users):
    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id, "2953864771")

    client = FakeOzonSellerClient([{"items": [_SAMPLE_ITEM], "total": 1, "page_count": 1}])
    outcome = sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client,
        date_from=date(2026, 8, 8), date_to=date(2026, 9, 4),
    )

    assert outcome.created == 1
    assert outcome.updated == 0
    assert outcome.errors == []

    row = db_session.query(SearchQueryStatistic).filter(SearchQueryStatistic.store_id == d["store_a"].id).one()
    assert row.ozon_sku == "2953864771"
    assert row.query_text == "обувница"
    assert row.product_id == product.id
    assert row.source == "ozon_seller_api"
    assert row.period_start == date(2026, 8, 8)
    assert row.period_end == date(2026, 9, 4)
    assert row.people_searched == 189229
    assert row.people_saw == 24834
    assert float(row.position_ozon) == 82
    assert float(row.conv_search_to_card_pct_ozon) == 3.03
    assert row.conv_search_to_order_pct_ozon is None
    assert row.ordered_units_by_query == 20
    assert float(row.ordered_sum_by_query_rub) == 71538.84
    assert row.query_index == 1


def test_second_run_with_same_period_updates_not_duplicates(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")

    client = FakeOzonSellerClient([
        {"items": [_SAMPLE_ITEM], "total": 1, "page_count": 1},
        {"items": [{**_SAMPLE_ITEM, "position": 55}], "total": 1, "page_count": 1},
    ])

    sync_search_query_details(db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4))
    outcome2 = sync_search_query_details(db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4))

    assert outcome2.created == 0
    assert outcome2.updated == 1
    rows = db_session.query(SearchQueryStatistic).filter(SearchQueryStatistic.store_id == d["store_a"].id).all()
    assert len(rows) == 1
    assert float(rows[0].position_ozon) == 55


def test_different_period_adds_new_snapshot_without_overwriting(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")

    client = FakeOzonSellerClient([
        {"items": [_SAMPLE_ITEM], "total": 1, "page_count": 1},
        {"items": [{**_SAMPLE_ITEM, "position": 55}], "total": 1, "page_count": 1},
    ])

    sync_search_query_details(db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    sync_search_query_details(db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 2), date_to=date(2026, 9, 1))

    rows = db_session.query(SearchQueryStatistic).filter(SearchQueryStatistic.store_id == d["store_a"].id).all()
    assert len(rows) == 2
    positions = sorted(float(r.position_ozon) for r in rows)
    assert positions == [55, 82]


def test_skus_are_batched_by_configured_size(db_session, two_stores_with_users, monkeypatch):
    from app.core import config as config_module
    config_module.get_settings.cache_clear()
    monkeypatch.setenv("SEARCH_QUERY_STATS_SKU_BATCH_SIZE", "2")
    config_module.get_settings.cache_clear()

    d = two_stores_with_users
    for sku in ["1", "2", "3"]:
        _make_product(db_session, d["store_a"].id, sku)

    client = FakeOzonSellerClient([{"items": [], "total": 0, "page_count": 1} for _ in range(2)])
    sync_search_query_details(db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))

    config_module.get_settings.cache_clear()

    assert [len(c["skus"]) for c in client.calls] == [2, 1]


def test_one_batch_failure_does_not_abort_the_others(db_session, two_stores_with_users, monkeypatch):
    from app.core import config as config_module
    config_module.get_settings.cache_clear()
    monkeypatch.setenv("SEARCH_QUERY_STATS_SKU_BATCH_SIZE", "1")
    config_module.get_settings.cache_clear()

    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "111")
    _make_product(db_session, d["store_a"].id, "2953864771")

    client = ErrorOnceThenOkClient([{"items": [_SAMPLE_ITEM], "total": 1, "page_count": 1}])
    outcome = sync_search_query_details(db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4))

    config_module.get_settings.cache_clear()

    assert outcome.created == 1
    assert any("500" in e for e in outcome.errors)


def test_page_count_over_one_is_reported_as_a_diagnostic(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")

    client = FakeOzonSellerClient([{"items": [_SAMPLE_ITEM], "total": 200, "page_count": 2}])
    outcome = sync_search_query_details(db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4))

    assert outcome.created == 1
    assert any("страниц" in e for e in outcome.errors)


def test_all_batches_returning_zero_items_is_reported_as_a_diagnostic(db_session, two_stores_with_users):
    """Regression test: Ozon can return HTTP 200 with an empty items array
    for every batch (no per-item error, no exception) — before this
    diagnostic, that silently produced outcome.errors == [] and a SUCCESS
    run reporting fetched=created=updated=0 with no explanation at all,
    exactly the reported symptom. A genuinely empty result across the whole
    run must be surfaced, not silently treated as a normal success."""
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")

    client = FakeOzonSellerClient([{"items": [], "total": 0, "page_count": 1}])
    outcome = sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4)
    )

    assert outcome.fetched == 0
    assert outcome.created == 0
    assert any("0 строк" in e for e in outcome.errors)


def test_nonzero_result_is_not_flagged_as_the_all_empty_diagnostic(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")

    client = FakeOzonSellerClient([{"items": [_SAMPLE_ITEM], "total": 1, "page_count": 1}])
    outcome = sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4)
    )

    assert outcome.errors == []


def test_period_too_wide_is_shrunk_and_retried_until_accepted(db_session, two_stores_with_users):
    """Regression test for the reported production bug: Ozon rejects a date
    range it considers too wide/too old OUTRIGHT (InvalidArgument, not an
    empty result) — confirmed live: a 30-day window ending 2 days ago
    worked, but a 92-day window ending 7 days ago (2026-06-01..2026-08-31)
    was rejected with "There is no data for the specified period". The sync
    must discover and use the largest accepted window automatically instead
    of failing the whole run or silently reporting 0 rows."""
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")

    client = PeriodLimitedClient(max_accepted_days=10, response={"items": [_SAMPLE_ITEM], "total": 1, "page_count": 1})
    outcome = sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client,
        date_from=date(2026, 6, 1), date_to=date(2026, 8, 31),  # the exact 92-day span from the report
    )

    assert outcome.created == 1
    assert client.calls[-1]["span"] <= 10  # the request that finally succeeded
    assert any("сокращён" in e for e in outcome.errors)  # visible to the user, not silent

    row = db_session.query(SearchQueryStatistic).filter(SearchQueryStatistic.store_id == d["store_a"].id).one()
    assert (row.period_end - row.period_start).days + 1 <= 10
    assert row.period_end == date(2026, 8, 31)  # date_to stays anchored; only date_from moves


def test_period_rejected_even_at_floor_reports_a_clear_error(db_session, two_stores_with_users):
    """If Ozon rejects the period even at the configured minimum, the sync
    must give up with a specific, actionable message — not loop forever and
    not report the generic "got 0 rows" diagnostic, which would be
    misleading (Ozon never actually returned a successful empty result)."""
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")

    client = PeriodLimitedClient(max_accepted_days=0, response={"items": [], "total": 0, "page_count": 1})
    outcome = sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client,
        date_from=date(2026, 6, 1), date_to=date(2026, 8, 31),
    )

    assert outcome.created == 0
    assert any("ограничение периода/тарифа Ozon" in e for e in outcome.errors)
    assert not any("0 строк" in e for e in outcome.errors)


def test_period_shrink_only_triggers_on_the_specific_ozon_period_error(db_session, two_stores_with_users):
    """A generic API error (e.g. a 500) on the first batch must not be
    mistaken for the period-too-wide condition — no shrink/retry, just the
    existing single-attempt-then-report-error behavior."""
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")

    client = ErrorOnceThenOkClient([{"items": [_SAMPLE_ITEM], "total": 1, "page_count": 1}])
    outcome = sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4)
    )

    assert outcome.created == 0
    assert any("500" in e for e in outcome.errors)
