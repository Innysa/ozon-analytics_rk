"""Tests for the orchestration service that pulls search-query-details data
from Ozon Seller API's POST /v1/analytics/product-queries/details (see
app.services.search_query_details_sync_service): SKU batching, upsert-by-
period history (no overwrite across different date ranges), field mapping
onto SearchQueryStatistic, and one batch's failure not aborting the others.
Uses a fake, duck-typed OzonSellerClient — the client's own request-building
behavior is already covered by test_ozon_seller_client.py."""
from datetime import date

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
    def __init__(self, responses: list[dict] | None = None):
        self.calls: list[dict] = []
        self._responses = responses or []

    def get_product_query_details(self, *, date_from, date_to, skus=None, limit_by_sku=15, page_size=100):
        self.calls.append(
            {"date_from": date_from, "date_to": date_to, "skus": list(skus or []), "limit_by_sku": limit_by_sku, "page_size": page_size}
        )
        if self._responses:
            return self._responses.pop(0)
        return {"items": [], "total": 0, "page_count": 1}


class ErrorOnceThenOkClient(FakeOzonSellerClient):
    def __init__(self, responses):
        super().__init__(responses)
        self._first = True

    def get_product_query_details(self, **kwargs):
        if self._first:
            self._first = False
            raise OzonAPIError("Ozon вернул ошибку сервера 500")
        return super().get_product_query_details(**kwargs)


def _make_product(db_session, store_id: str, sku: str):
    product = Product(store_id=store_id, ozon_sku=sku, name=f"Товар {sku}")
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
    and the skip must be visible in outcome.errors rather than silent."""
    d = two_stores_with_users
    _make_product(db_session, d["store_a"].id, "2953864771")
    _make_product(db_session, d["store_a"].id, "0")

    client = FakeOzonSellerClient([{"items": [_SAMPLE_ITEM], "total": 1, "page_count": 1}])
    outcome = sync_search_query_details(
        db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 8, 8), date_to=date(2026, 9, 4)
    )

    assert len(client.calls) == 1
    assert client.calls[0]["skus"] == ["2953864771"]
    assert outcome.created == 1
    assert any("0" in e and "SKU" in e for e in outcome.errors)


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
