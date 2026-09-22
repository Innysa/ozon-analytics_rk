"""Tests for the orders/revenue aggregation over Ozon Seller API postings
(app.services.order_daily_sync_service). Fixtures are built from the real
confirmed shapes in backend/scripts/debug_orders_finance_api.py's own
docstring / the real account dump that confirmed them: sku/offer_id/name/
quantity/price on products[], old_price/commission_amount matched by
product_id in financial_data.products[], status "delivered"/"cancelled" as
real observed values."""
from datetime import date, timedelta

from app.services.ozon.schemas import OzonPostingItem, OzonPostingProductItem
from app.services.order_daily_sync_service import (
    SyncOutcome,
    _count_missing_commission_units,
    _date_chunks,
    _fetch_all_postings,
    _parse_in_process_at,
    aggregate_postings_by_day,
    aggregate_postings_by_sku_and_day,
    commission_missing_units_note,
    fetched_by_schema_note,
)


def _posting(
    *, status: str, in_process_at: str, sku: int, price: str, old_price: float, commission: float,
    quantity: int = 1, posting_number: str = "123-0001-1",
) -> OzonPostingItem:
    return OzonPostingItem(
        posting_number=posting_number,
        status=status,
        in_process_at=in_process_at,
        products=[OzonPostingProductItem(sku=sku, offer_id="art", name="Товар", quantity=quantity, price=price)],
        financial_data={"products": [{"product_id": sku, "old_price": old_price, "price": float(price), "commission_amount": commission}]},
    )


def test_parse_in_process_at_buckets_by_moscow_day_not_utc_day():
    """A posting at 21:30 UTC on Sept 2 is 00:30 MSK on Sept 3 — Ozon's
    own cabinet reports by Moscow day, so bucketing by raw UTC date would
    silently put this order a day early relative to Ozon's own count
    (same bug class already confirmed and fixed on the frontend, see
    isoDate()'s own docstring)."""
    assert _parse_in_process_at("2026-09-02T21:30:00.000000Z") == date(2026, 9, 3)
    # Just before the MSK boundary — still the same UTC AND Moscow day.
    assert _parse_in_process_at("2026-09-02T20:59:59.000000Z") == date(2026, 9, 2)
    # Comfortably mid-Moscow-day — same date either way, sanity check.
    assert _parse_in_process_at("2026-09-02T10:00:00.000000Z") == date(2026, 9, 2)


def test_aggregate_postings_by_day_buckets_late_utc_night_order_into_next_moscow_day():
    posting = _posting(
        status="delivered", in_process_at="2026-09-02T22:00:00.000000Z",  # 01:00 MSK on Sept 3
        sku=1, price="100.00", old_price=100.0, commission=-10,
    )
    daily = aggregate_postings_by_day([posting], cost_by_sku={})
    assert date(2026, 9, 3) in daily
    assert date(2026, 9, 2) not in daily


def test_delivered_posting_with_known_cost_price():
    posting = _posting(status="delivered", in_process_at="2026-09-02T00:41:55.354532Z", sku=5141625873, price="1200.00", old_price=6936.08, commission=-624)

    daily = aggregate_postings_by_day([posting], cost_by_sku={"5141625873": 400.0})

    bucket = daily[date(2026, 9, 2)]
    assert bucket["ordered_units"] == 1
    assert bucket["ordered_sum_rub"] == 6936.08
    assert bucket["ordered_sum_discounted_rub"] == 1200.0
    assert bucket["delivered_units"] == 1
    assert bucket["delivered_sum_rub"] == 1200.0
    assert bucket["cost_of_delivered_rub"] == 400.0
    assert bucket["cost_of_delivered_known_units"] == 1
    assert bucket["cancelled_units"] == 0
    assert bucket["unfinished_units"] == 0
    assert bucket["commission_rub"] == -624


def test_delivered_posting_without_known_cost_price_leaves_cost_at_zero():
    posting = _posting(status="delivered", in_process_at="2026-09-02T00:41:55.354532Z", sku=5141625873, price="1200.00", old_price=6936.08, commission=-624)

    daily = aggregate_postings_by_day([posting], cost_by_sku={})

    bucket = daily[date(2026, 9, 2)]
    assert bucket["delivered_units"] == 1
    assert bucket["cost_of_delivered_rub"] == 0
    assert bucket["cost_of_delivered_known_units"] == 0


def test_seller_price_known_feeds_correct_spp_base_not_old_price():
    """CONFIRMED root cause (2026-09-20, real account) of README's "Известная
    проблема" for «СПП (расчёт)»: old_price (Ozon's "Цена до скидки") is a
    reference/strikethrough price, not the base Ozon's own cabinet uses for
    its СПП% — that's "Ваша цена" (seller_price_by_sku here, sourced from
    Product.price_rub). Real example: old_price=13000 (matched the user's
    own "Цены и акции" export's "Цена до скидки" exactly), seller price
    (Product.price_rub) 6000 (close to but not identical to that export's
    "Ваша цена" 6200 — ordinary price-change drift, not a wrong field)."""
    posting = _posting(status="delivered", in_process_at="2026-09-19T10:00:00.000000Z", sku=1, price="3791.00", old_price=13000.0, commission=-300)

    daily = aggregate_postings_by_day([posting], cost_by_sku={}, seller_price_by_sku={"1": 6000.0})

    bucket = daily[date(2026, 9, 19)]
    assert bucket["ordered_sum_rub"] == 13000.0  # unchanged — still "Цена до скидки", not repurposed
    assert bucket["ordered_sum_seller_price_rub"] == 6000.0
    assert bucket["ordered_sum_discounted_for_known_seller_price_rub"] == 3791.0
    assert bucket["ordered_units_with_known_seller_price"] == 1


def test_seller_price_unknown_sku_excluded_from_seller_price_totals_not_defaulted():
    """A SKU absent from seller_price_by_sku (never synced into our Product
    table, or deleted) must NOT silently default to 0 or to price/old_price
    — that would corrupt the ratio. It's simply excluded, same "known vs
    unknown" split as cost_of_delivered_known_units."""
    posting = _posting(status="delivered", in_process_at="2026-09-19T10:00:00.000000Z", sku=999, price="500.00", old_price=1000.0, commission=-50)

    daily = aggregate_postings_by_day([posting], cost_by_sku={}, seller_price_by_sku={})

    bucket = daily[date(2026, 9, 19)]
    assert bucket["ordered_units"] == 1  # still counted everywhere else
    assert bucket["ordered_sum_seller_price_rub"] == 0
    assert bucket["ordered_sum_discounted_for_known_seller_price_rub"] == 0
    assert bucket["ordered_units_with_known_seller_price"] == 0


def test_day_aware_seller_price_preferred_over_flat_snapshot():
    """ADDED 2026-09-22 (see ProductPriceDailySnapshot's own docstring):
    seller_price_by_sku_and_date — the real price on THIS order's own day —
    must win over the flat (today's-snapshot) seller_price_by_sku when both
    are available for the same (sku, day), since the whole point is that
    Ozon's own seller price genuinely differs day to day."""
    posting = _posting(status="delivered", in_process_at="2026-09-01T10:00:00.000000Z", sku=1, price="1500.00", old_price=3000.0, commission=-100)

    daily = aggregate_postings_by_day(
        [posting], cost_by_sku={},
        seller_price_by_sku={"1": 6000.0},  # today's snapshot — must be ignored, a day-specific value exists
        seller_price_by_sku_and_date={("1", date(2026, 9, 1)): 2400.0},
    )

    bucket = daily[date(2026, 9, 1)]
    assert bucket["ordered_sum_seller_price_rub"] == 2400.0


def test_day_aware_seller_price_falls_back_to_flat_snapshot_for_uncovered_day():
    """A day the history doesn't cover yet (e.g. before daily snapshotting
    started) still gets the OLD flat-snapshot approximation, not "unknown"
    — the day-aware map only IMPROVES accuracy where it has data, never
    regresses coverage relative to the pre-existing behavior."""
    posting = _posting(status="delivered", in_process_at="2026-08-01T10:00:00.000000Z", sku=1, price="1500.00", old_price=3000.0, commission=-100)

    daily = aggregate_postings_by_day(
        [posting], cost_by_sku={},
        seller_price_by_sku={"1": 6000.0},
        seller_price_by_sku_and_date={("1", date(2026, 9, 1)): 2400.0},  # a different day only
    )

    bucket = daily[date(2026, 8, 1)]
    assert bucket["ordered_sum_seller_price_rub"] == 6000.0


def test_by_sku_and_day_prefers_day_aware_seller_price_too():
    posting = _posting(status="delivered", in_process_at="2026-09-01T10:00:00.000000Z", sku=1, price="1500.00", old_price=3000.0, commission=-100)

    by_sku_day = aggregate_postings_by_sku_and_day(
        [posting],
        seller_price_by_sku={"1": 6000.0},
        seller_price_by_sku_and_date={("1", date(2026, 9, 1)): 2400.0},
    )

    bucket = by_sku_day[("1", date(2026, 9, 1))]
    assert bucket["ordered_sum_seller_price_rub"] == 2400.0


def test_cancelled_posting_goes_to_cancelled_bucket_not_delivered():
    posting = _posting(status="cancelled", in_process_at="2026-09-03T10:00:00.000000Z", sku=1, price="500.00", old_price=500.0, commission=0)

    daily = aggregate_postings_by_day([posting], cost_by_sku={})

    bucket = daily[date(2026, 9, 3)]
    assert bucket["cancelled_units"] == 1
    assert bucket["cancelled_sum_rub"] == 500.0
    assert bucket["delivered_units"] == 0
    assert bucket["unfinished_units"] == 0


def test_unknown_status_is_bucketed_as_unfinished():
    """Ozon has many more granular statuses than "delivered"/"cancelled"
    (e.g. "awaiting_deliver") — this module deliberately buckets anything
    else as "unfinished" rather than guessing a finer classification."""
    posting = _posting(status="awaiting_deliver", in_process_at="2026-09-03T10:00:00.000000Z", sku=1, price="500.00", old_price=500.0, commission=0)

    daily = aggregate_postings_by_day([posting], cost_by_sku={})

    bucket = daily[date(2026, 9, 3)]
    assert bucket["unfinished_units"] == 1
    assert bucket["delivered_units"] == 0
    assert bucket["cancelled_units"] == 0


def test_multiple_postings_same_day_are_summed():
    p1 = _posting(status="delivered", in_process_at="2026-09-02T01:00:00.000000Z", sku=1, price="100.00", old_price=100.0, commission=-10)
    p2 = _posting(status="delivered", in_process_at="2026-09-02T20:00:00.000000Z", sku=1, price="100.00", old_price=100.0, commission=-10)

    daily = aggregate_postings_by_day([p1, p2], cost_by_sku={})

    assert len(daily) == 1
    bucket = daily[date(2026, 9, 2)]
    assert bucket["ordered_units"] == 2
    assert bucket["delivered_units"] == 2
    assert bucket["commission_rub"] == -20


def test_posting_without_financial_data_falls_back_to_price_as_old_price():
    posting = OzonPostingItem(
        posting_number="x", status="delivered", in_process_at="2026-09-02T00:00:00Z",
        products=[OzonPostingProductItem(sku=1, offer_id="a", name="Товар", quantity=1, price="300.00")],
        financial_data=None,
    )

    daily = aggregate_postings_by_day([posting], cost_by_sku={})

    bucket = daily[date(2026, 9, 2)]
    assert bucket["ordered_sum_rub"] == 300.0
    assert bucket["ordered_sum_discounted_rub"] == 300.0
    assert bucket["commission_rub"] == 0


def test_posting_without_in_process_at_is_skipped_not_crashed():
    posting = OzonPostingItem(posting_number="x", status="delivered", in_process_at=None, products=[])

    daily = aggregate_postings_by_day([posting], cost_by_sku={})

    assert daily == {}


def test_count_missing_commission_units_counts_missing_financial_data():
    """Regression, CONFIRMED 2026-09-12: a real account's MarginBlock.
    commission_rub stayed ~41% below CashFlowStatementPeriod's own accrual
    commission_amount even after backfilling every fully-missing sync
    window — meaning some ALREADY-SYNCED postings are silently contributing
    zero commission (see test_posting_without_financial_data_falls_back_
    to_price_as_old_price above, which shows this happens by design when
    financial_data is None). This counts exactly those units so the gap is
    measurable instead of merely suspected."""
    no_financial_data = OzonPostingItem(
        posting_number="a", status="delivered", in_process_at="2026-09-02T00:00:00Z",
        products=[OzonPostingProductItem(sku=1, offer_id="a", name="Товар", quantity=2, price="300.00")],
        financial_data=None,
    )
    sku_not_in_financial_data = OzonPostingItem(
        posting_number="b", status="delivered", in_process_at="2026-09-02T00:00:00Z",
        products=[OzonPostingProductItem(sku=2, offer_id="b", name="Товар2", quantity=1, price="100.00")],
        financial_data={"products": [{"product_id": 999, "old_price": 100.0, "commission_amount": -10}]},
    )
    with_commission = OzonPostingItem(
        posting_number="c", status="delivered", in_process_at="2026-09-02T00:00:00Z",
        products=[OzonPostingProductItem(sku=3, offer_id="c", name="Товар3", quantity=1, price="100.00")],
        financial_data={"products": [{"product_id": 3, "old_price": 100.0, "commission_amount": -10}]},
    )
    zero_price_line = OzonPostingItem(
        posting_number="d", status="delivered", in_process_at="2026-09-02T00:00:00Z",
        products=[OzonPostingProductItem(sku=4, offer_id="d", name="Товар4", quantity=1, price="0.00")],
        financial_data=None,
    )

    missing = _count_missing_commission_units(
        [no_financial_data, sku_not_in_financial_data, with_commission, zero_price_line]
    )

    assert missing == 3  # 2 units (no_financial_data) + 1 unit (sku_not_in_financial_data)


def test_commission_missing_units_note_absent_when_zero():
    assert commission_missing_units_note(SyncOutcome()) is None


def test_commission_missing_units_note_present_when_nonzero():
    note = commission_missing_units_note(SyncOutcome(commission_missing_units=7))
    assert "7" in note


def test_fetched_by_schema_note_absent_when_empty():
    assert fetched_by_schema_note(SyncOutcome()) is None


def test_fetched_by_schema_note_present_with_breakdown():
    """Regression, CONFIRMED 2026-09-12 (real account "Дельта дом"):
    SyncRun.items_fetched combines FBO+FBS into one number, so "получено
    556, создано 0, обновлено 1" was misread as "FBS has real data" when
    it was equally consistent with "all 556 were FBO (already had a row,
    hence the 1 update) and FBS genuinely fetched 0" — this note exists to
    make that distinction directly readable instead of re-derived from
    created/updated counts."""
    note = fetched_by_schema_note(SyncOutcome(fetched_by_schema={"FBO": 556, "FBS": 0}))
    assert "FBO=556" in note
    assert "FBS=0" in note


def test_by_sku_and_day_keeps_different_skus_separate():
    """The store-level aggregate_postings_by_day sums across every SKU into
    one bucket per day; the per-SKU variant must keep them apart instead of
    collapsing back into a single total."""
    p1 = _posting(status="delivered", in_process_at="2026-09-02T01:00:00.000000Z", sku=111, price="100.00", old_price=100.0, commission=-10)
    p2 = _posting(status="delivered", in_process_at="2026-09-02T02:00:00.000000Z", sku=222, price="50.00", old_price=50.0, commission=-5)

    by_sku_day = aggregate_postings_by_sku_and_day([p1, p2])

    assert set(by_sku_day.keys()) == {("111", date(2026, 9, 2)), ("222", date(2026, 9, 2))}
    assert by_sku_day[("111", date(2026, 9, 2))]["ordered_units"] == 1
    assert by_sku_day[("111", date(2026, 9, 2))]["delivered_sum_rub"] == 100.0
    assert by_sku_day[("222", date(2026, 9, 2))]["delivered_sum_rub"] == 50.0


def test_by_sku_and_day_seller_price_known_and_unknown_skus():
    """Same СПП-base fix as the store-level aggregate_postings_by_day (see
    test_seller_price_known_feeds_correct_spp_base_not_old_price above), for
    «РНП Товары»'s per-product daily breakdown table — SKU 111's price is
    known, SKU 222's isn't, and each must be handled independently even
    though they share the same day bucket key structure."""
    p1 = _posting(status="delivered", in_process_at="2026-09-02T01:00:00.000000Z", sku=111, price="3791.00", old_price=13000.0, commission=-300)
    p2 = _posting(status="delivered", in_process_at="2026-09-02T02:00:00.000000Z", sku=222, price="500.00", old_price=1000.0, commission=-50)

    by_sku_day = aggregate_postings_by_sku_and_day([p1, p2], seller_price_by_sku={"111": 6000.0})

    known = by_sku_day[("111", date(2026, 9, 2))]
    assert known["ordered_sum_seller_price_rub"] == 6000.0
    assert known["ordered_sum_discounted_for_known_seller_price_rub"] == 3791.0
    assert known["ordered_units_with_known_seller_price"] == 1

    unknown = by_sku_day[("222", date(2026, 9, 2))]
    assert unknown["ordered_units"] == 1  # still counted everywhere else
    assert unknown["ordered_sum_seller_price_rub"] == 0
    assert unknown["ordered_sum_discounted_for_known_seller_price_rub"] == 0
    assert unknown["ordered_units_with_known_seller_price"] == 0


def test_by_sku_and_day_same_sku_multiple_postings_summed():
    p1 = _posting(status="delivered", in_process_at="2026-09-02T01:00:00.000000Z", sku=111, price="100.00", old_price=100.0, commission=-10)
    p2 = _posting(status="delivered", in_process_at="2026-09-02T20:00:00.000000Z", sku=111, price="100.00", old_price=100.0, commission=-10)

    by_sku_day = aggregate_postings_by_sku_and_day([p1, p2])

    bucket = by_sku_day[("111", date(2026, 9, 2))]
    assert bucket["ordered_units"] == 2
    assert bucket["delivered_units"] == 2
    assert bucket["commission_rub"] == -20


def test_by_sku_and_day_cancelled_and_unfinished_buckets():
    cancelled = _posting(status="cancelled", in_process_at="2026-09-03T10:00:00.000000Z", sku=1, price="500.00", old_price=500.0, commission=0)
    unfinished = _posting(status="awaiting_deliver", in_process_at="2026-09-03T10:00:00.000000Z", sku=2, price="300.00", old_price=300.0, commission=0)

    by_sku_day = aggregate_postings_by_sku_and_day([cancelled, unfinished])

    assert by_sku_day[("1", date(2026, 9, 3))]["cancelled_units"] == 1
    assert by_sku_day[("1", date(2026, 9, 3))]["delivered_units"] == 0
    assert by_sku_day[("2", date(2026, 9, 3))]["unfinished_units"] == 1


def test_by_sku_and_day_skips_posting_without_in_process_at():
    posting = OzonPostingItem(posting_number="x", status="delivered", in_process_at=None, products=[])

    by_sku_day = aggregate_postings_by_sku_and_day([posting])

    assert by_sku_day == {}


def test_by_sku_and_day_skips_line_with_no_sku():
    posting = OzonPostingItem(
        posting_number="x", status="delivered", in_process_at="2026-09-02T00:00:00Z",
        products=[OzonPostingProductItem(sku=None, offer_id="a", name="Товар", quantity=1, price="300.00")],
    )

    by_sku_day = aggregate_postings_by_sku_and_day([posting])

    assert by_sku_day == {}


class _FakePaginatedFetch:
    """Simulates has_next pagination — confirmed real behavior: a real
    account's transaction list came back with exactly page_size rows for a
    7-day window, meaning more existed beyond that page."""

    def __init__(self, pages: list[list[OzonPostingItem]], *, has_next_overrides: dict[int, bool] | None = None):
        self._pages = pages
        # Lets a test simulate Ozon's own has_next being WRONG for a given
        # page index — see test_fetch_all_postings_flags_truncation_when_
        # ozon_has_next_lies below, matching the real 2026-09-22 incident
        # (has_next=False even though more/duplicate data was reachable).
        self._has_next_overrides = has_next_overrides or {}
        self.calls: list[dict] = []

    def __call__(self, *, date_from, date_to, offset, limit):
        from app.services.ozon.schemas import OzonPostingListResponse, OzonPostingListResult

        self.calls.append({"date_from": date_from, "date_to": date_to, "offset": offset, "limit": limit})
        page_index = offset // limit
        postings = self._pages[page_index] if page_index < len(self._pages) else []
        if page_index in self._has_next_overrides:
            has_next = self._has_next_overrides[page_index]
        else:
            has_next = page_index < len(self._pages) - 1
        return OzonPostingListResponse(result=OzonPostingListResult(postings=postings, has_next=has_next))


def test_fetch_all_postings_follows_has_next_pagination():
    page1 = [_posting(status="delivered", in_process_at="2026-09-02T00:00:00Z", sku=1, price="1", old_price=1, commission=0, posting_number="p1")]
    page2 = [_posting(status="delivered", in_process_at="2026-09-03T00:00:00Z", sku=2, price="1", old_price=1, commission=0, posting_number="p2")]
    fetch = _FakePaginatedFetch([page1, page2])

    postings, truncated = _fetch_all_postings(fetch, date_from="2026-09-01T00:00:00Z", date_to="2026-09-07T23:59:59Z")

    assert len(postings) == 2
    assert truncated is False
    assert len(fetch.calls) == 2
    assert fetch.calls[0]["offset"] == 0
    assert fetch.calls[1]["offset"] == fetch.calls[0]["limit"]


def test_fetch_all_postings_stops_when_has_next_is_false():
    page1 = [_posting(status="delivered", in_process_at="2026-09-02T00:00:00Z", sku=1, price="1", old_price=1, commission=0)]
    fetch = _FakePaginatedFetch([page1])

    postings, truncated = _fetch_all_postings(fetch, date_from="2026-09-01T00:00:00Z", date_to="2026-09-07T23:59:59Z")

    assert len(postings) == 1
    assert truncated is False
    assert len(fetch.calls) == 1


def test_fetch_all_postings_flags_truncation_when_ozon_has_next_lies():
    """CONFIRMED 2026-09-22 on a real account (store "Комфорт дом"): FBO's
    own fetched-per-run count was EXACTLY PAGE_LIMIT × chunk-count on FOUR
    SEPARATE nightly runs covering DIFFERENT rolling windows — has_next was
    apparently never true for this store's FBO postings even though the
    true volume exceeded PAGE_LIMIT per chunk, silently losing everything
    past page 1. The old code trusted has_next=False outright; this probes
    one page further whenever the page it just got was FULL, and treats a
    repeated leading posting_number at that next offset as confirmation
    more data existed but couldn't actually be reached (offset not really
    advancing the window) — not a genuinely complete fetch."""
    from app.services.order_daily_sync_service import PAGE_LIMIT

    full_page = [
        _posting(status="delivered", in_process_at="2026-09-02T00:00:00Z", sku=n, price="1", old_price=1, commission=0, posting_number=f"p{n}")
        for n in range(PAGE_LIMIT)
    ]
    # Ozon reports has_next=False on the very first (full) page — exactly
    # the real incident — but probing the next offset anyway reveals the
    # SAME data again (offset didn't move the window).
    fetch = _FakePaginatedFetch([full_page, full_page], has_next_overrides={0: False})

    postings, truncated = _fetch_all_postings(fetch, date_from="2026-09-01T00:00:00Z", date_to="2026-09-07T23:59:59Z")

    assert len(postings) == PAGE_LIMIT  # the repeated page is NOT double-counted
    assert truncated is True
    assert len(fetch.calls) == 2  # the probe past has_next=False actually happened


def test_fetch_all_postings_not_truncated_when_full_page_is_genuinely_the_whole_dataset():
    """A full page whose true size just happens to equal PAGE_LIMIT exactly
    must NOT be flagged — probing past it and getting a genuinely EMPTY
    page (not a repeat) confirms there really was nothing more to fetch."""
    from app.services.order_daily_sync_service import PAGE_LIMIT

    full_page = [
        _posting(status="delivered", in_process_at="2026-09-02T00:00:00Z", sku=n, price="1", old_price=1, commission=0, posting_number=f"p{n}")
        for n in range(PAGE_LIMIT)
    ]
    fetch = _FakePaginatedFetch([full_page], has_next_overrides={0: False})  # only one page exists — the next probe returns []

    postings, truncated = _fetch_all_postings(fetch, date_from="2026-09-01T00:00:00Z", date_to="2026-09-07T23:59:59Z")

    assert len(postings) == PAGE_LIMIT
    assert truncated is False
    assert len(fetch.calls) == 2  # still probed once past the full page, found it genuinely empty


def test_fetch_all_postings_not_truncated_when_full_page_is_followed_by_new_data():
    """A full first page is NOT itself truncation — only failing to reach a
    genuinely shorter/empty page (or looping) is. Continuing past a full
    page even though Ozon said has_next=False is what recovers this case."""
    from app.services.order_daily_sync_service import PAGE_LIMIT

    page1 = [
        _posting(status="delivered", in_process_at="2026-09-02T00:00:00Z", sku=n, price="1", old_price=1, commission=0, posting_number=f"p{n}")
        for n in range(PAGE_LIMIT)
    ]
    page2 = [_posting(status="delivered", in_process_at="2026-09-03T00:00:00Z", sku=99999, price="1", old_price=1, commission=0, posting_number="p_last")]
    fetch = _FakePaginatedFetch([page1, page2])

    postings, truncated = _fetch_all_postings(fetch, date_from="2026-09-01T00:00:00Z", date_to="2026-09-07T23:59:59Z")

    assert len(postings) == PAGE_LIMIT + 1
    assert truncated is False


def test_date_chunks_splits_exact_multiple():
    chunks = _date_chunks(date(2026, 9, 1), date(2026, 9, 10), 5)
    assert chunks == [(date(2026, 9, 1), date(2026, 9, 5)), (date(2026, 9, 6), date(2026, 9, 10))]


def test_date_chunks_splits_with_remainder():
    chunks = _date_chunks(date(2026, 9, 1), date(2026, 9, 12), 5)
    assert chunks == [
        (date(2026, 9, 1), date(2026, 9, 5)),
        (date(2026, 9, 6), date(2026, 9, 10)),
        (date(2026, 9, 11), date(2026, 9, 12)),
    ]


def test_date_chunks_single_day_range_is_one_chunk():
    chunks = _date_chunks(date(2026, 9, 1), date(2026, 9, 1), 5)
    assert chunks == [(date(2026, 9, 1), date(2026, 9, 1))]


def test_date_chunks_chunk_days_larger_than_range_is_one_chunk():
    chunks = _date_chunks(date(2026, 9, 1), date(2026, 9, 3), 30)
    assert chunks == [(date(2026, 9, 1), date(2026, 9, 3))]


def test_date_chunks_never_exceeds_chunk_days_per_chunk():
    chunks = _date_chunks(date(2026, 8, 1), date(2026, 9, 11), 5)
    for start, end in chunks:
        assert (end - start).days < 5
    # covers the whole range with no gaps or overlaps
    assert chunks[0][0] == date(2026, 8, 1)
    assert chunks[-1][1] == date(2026, 9, 11)
    for (_, prev_end), (next_start, _) in zip(chunks, chunks[1:]):
        assert next_start == prev_end + timedelta(days=1)


def test_sync_pauses_between_chunk_requests(db_session, monkeypatch):
    """Regression test for a real production finding (2026-09-11): with
    smaller 5-day chunks, a real account's FBO sync still 429'd, but on a
    DIFFERENT random subset of chunks each run — evidence for a request-RATE
    quota (more chunk requests per run = more chances to trip it), not a
    per-request weight limit. ORDER_STATS_SYNC_CHUNK_PAUSE_SECONDS adds a
    deliberate pause between chunk requests to directly reduce that rate."""
    import app.core.config as config_module
    import app.services.order_daily_sync_service as svc
    from app.models.store import Store
    from app.services.ozon.schemas import OzonPostingListResponse, OzonPostingListResult

    monkeypatch.setenv("ORDER_STATS_SYNC_CHUNK_PAUSE_SECONDS", "2")
    monkeypatch.setenv("ORDER_STATS_SYNC_CHUNK_DAYS", "5")  # pinned so this test doesn't depend on the ambient default
    config_module.get_settings.cache_clear()

    sleep_calls: list[float] = []
    monkeypatch.setattr(svc.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    class _FakeEmptyClient:
        def list_fbo_postings(self, *, date_from, date_to, offset=0, limit=1000):
            return OzonPostingListResponse(result=OzonPostingListResult(postings=[], has_next=False))

        list_fbs_postings = list_fbo_postings

    store = Store(name="Test")
    db_session.add(store)
    db_session.flush()

    try:
        svc.sync_order_daily_statistics(
            db_session, store_id=store.id, client=_FakeEmptyClient(),
            date_from=date(2026, 9, 1), date_to=date(2026, 9, 10),
        )
    finally:
        config_module.get_settings.cache_clear()

    # 10-day range / 5-day chunks = 2 chunks per schema x 2 schemas (FBO, FBS) = 4 pauses.
    assert sleep_calls == [2.0, 2.0, 2.0, 2.0]


def test_sync_skips_pause_when_configured_to_zero(db_session, monkeypatch):
    import app.core.config as config_module
    import app.services.order_daily_sync_service as svc
    from app.models.store import Store
    from app.services.ozon.schemas import OzonPostingListResponse, OzonPostingListResult

    monkeypatch.setenv("ORDER_STATS_SYNC_CHUNK_PAUSE_SECONDS", "0")
    config_module.get_settings.cache_clear()

    sleep_calls: list[float] = []
    monkeypatch.setattr(svc.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    class _FakeEmptyClient:
        def list_fbo_postings(self, *, date_from, date_to, offset=0, limit=1000):
            return OzonPostingListResponse(result=OzonPostingListResult(postings=[], has_next=False))

        list_fbs_postings = list_fbo_postings

    store = Store(name="Test")
    db_session.add(store)
    db_session.flush()

    try:
        svc.sync_order_daily_statistics(
            db_session, store_id=store.id, client=_FakeEmptyClient(),
            date_from=date(2026, 9, 1), date_to=date(2026, 9, 3),
        )
    finally:
        config_module.get_settings.cache_clear()

    assert sleep_calls == []


def test_sync_pause_doubles_on_rate_limit_and_caps_at_max(db_session, monkeypatch):
    """Regression test for a real production finding (2026-09-11): a FLAT
    pause between chunks (tried at 3s) still 429'd — on a different random
    subset of chunks each run, consistent with a rate quota rather than a
    fixed number of "safe" chunks. The pause must actually escalate within
    a run that keeps tripping the quota, not sit at whatever flat guess was
    configured — see ORDER_STATS_SYNC_CHUNK_PAUSE_SECONDS's own comment.
    FBO here always 429s (every chunk exhausts its own retry budget and
    still fails) -> pause should double each time, capped at the max, and
    that elevated pause should carry over into FBS (shared for the whole
    run, not reset per schema)."""
    import app.core.config as config_module
    import app.services.order_daily_sync_service as svc
    from app.models.store import Store
    from app.services.ozon.exceptions import OzonRateLimited
    from app.services.ozon.schemas import OzonPostingListResponse, OzonPostingListResult

    monkeypatch.setenv("ORDER_STATS_SYNC_CHUNK_PAUSE_SECONDS", "1")
    monkeypatch.setenv("ORDER_STATS_SYNC_CHUNK_PAUSE_MAX_SECONDS", "4")
    monkeypatch.setenv("ORDER_STATS_SYNC_CHUNK_DAYS", "5")  # pinned so this test doesn't depend on the ambient default
    config_module.get_settings.cache_clear()

    sleep_calls: list[float] = []
    monkeypatch.setattr(svc.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    class _FakeAlwaysRateLimitedFbo:
        def list_fbo_postings(self, *, date_from, date_to, offset=0, limit=1000):
            raise OzonRateLimited("Ozon вернул 429 Too Many Requests")

        def list_fbs_postings(self, *, date_from, date_to, offset=0, limit=1000):
            return OzonPostingListResponse(result=OzonPostingListResult(postings=[], has_next=False))

    store = Store(name="Test")
    db_session.add(store)
    db_session.flush()

    try:
        outcome = svc.sync_order_daily_statistics(
            db_session, store_id=store.id, client=_FakeAlwaysRateLimitedFbo(),
            date_from=date(2026, 9, 1), date_to=date(2026, 9, 15),  # 15 days / 5-day chunks = 3 chunks
        )
    finally:
        config_module.get_settings.cache_clear()

    assert len(outcome.errors) == 3  # all 3 FBO chunks failed
    # FBO: 1 -> 2 -> 4 (capped, 4*2=8 > max=4). FBS then inherits the
    # already-elevated 4s pause for its own 3 (successful) chunks.
    assert sleep_calls == [2.0, 4.0, 4.0, 4.0, 4.0, 4.0]


def test_full_sync_prefers_price_snapshot_of_the_orders_own_day(db_session, monkeypatch):
    """End-to-end regression test for the 2026-09-22 fix (see
    ProductPriceDailySnapshot's own docstring): a store with a HISTORICAL
    price snapshot that differs from Product's CURRENT price_rub must get
    the historical value in ordered_sum_seller_price_rub for an order
    placed on that historical day — not today's (wrong, drifted) price."""
    import app.services.order_daily_sync_service as svc
    from app.models.order_daily_statistic import OrderDailyStatistic
    from app.models.product import Product
    from app.models.product_price_daily_snapshot import ProductPriceDailySnapshot
    from app.models.store import Store
    from app.services.ozon.schemas import OzonPostingListResponse, OzonPostingListResult

    store = Store(name="Test")
    db_session.add(store)
    db_session.flush()
    db_session.add(Product(store_id=store.id, ozon_sku="1", name="Товар", price_rub=6000.0))
    db_session.add(ProductPriceDailySnapshot(store_id=store.id, ozon_sku="1", date=date(2026, 9, 1), price_rub=2400.0))
    db_session.commit()

    historical_posting = _posting(
        status="delivered", in_process_at="2026-09-01T10:00:00.000000Z", sku=1, price="1500.00", old_price=3000.0, commission=-100,
    )

    class _FakeClient:
        def list_fbo_postings(self, *, date_from, date_to, offset=0, limit=1000):
            if offset == 0:
                return OzonPostingListResponse(result=OzonPostingListResult(postings=[historical_posting], has_next=False))
            return OzonPostingListResponse(result=OzonPostingListResult(postings=[], has_next=False))

        def list_fbs_postings(self, *, date_from, date_to, offset=0, limit=1000):
            return OzonPostingListResponse(result=OzonPostingListResult(postings=[], has_next=False))

    svc.sync_order_daily_statistics(
        db_session, store_id=store.id, client=_FakeClient(),
        date_from=date(2026, 9, 1), date_to=date(2026, 9, 1),
    )
    db_session.commit()

    stat = (
        db_session.query(OrderDailyStatistic)
        .filter(OrderDailyStatistic.store_id == store.id, OrderDailyStatistic.date == date(2026, 9, 1))
        .first()
    )
    assert stat is not None
    # 2400 (that day's real snapshot), NOT 6000 (Product's current price_rub).
    assert float(stat.ordered_sum_seller_price_rub) == 2400.0
