"""Tests for the orders/revenue aggregation over Ozon Seller API postings
(app.services.order_daily_sync_service). Fixtures are built from the real
confirmed shapes in backend/scripts/debug_orders_finance_api.py's own
docstring / the real account dump that confirmed them: sku/offer_id/name/
quantity/price on products[], old_price/commission_amount matched by
product_id in financial_data.products[], status "delivered"/"cancelled" as
real observed values."""
from datetime import date

from app.services.ozon.schemas import OzonPostingItem, OzonPostingProductItem
from app.services.order_daily_sync_service import aggregate_postings_by_day, aggregate_postings_by_sku_and_day, _fetch_all_postings


def _posting(*, status: str, in_process_at: str, sku: int, price: str, old_price: float, commission: float, quantity: int = 1) -> OzonPostingItem:
    return OzonPostingItem(
        posting_number="123-0001-1",
        status=status,
        in_process_at=in_process_at,
        products=[OzonPostingProductItem(sku=sku, offer_id="art", name="Товар", quantity=quantity, price=price)],
        financial_data={"products": [{"product_id": sku, "old_price": old_price, "price": float(price), "commission_amount": commission}]},
    )


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

    def __init__(self, pages: list[list[OzonPostingItem]]):
        self._pages = pages
        self.calls: list[dict] = []

    def __call__(self, *, date_from, date_to, offset, limit):
        from app.services.ozon.schemas import OzonPostingListResponse, OzonPostingListResult

        self.calls.append({"date_from": date_from, "date_to": date_to, "offset": offset, "limit": limit})
        page_index = offset // limit
        postings = self._pages[page_index] if page_index < len(self._pages) else []
        has_next = page_index < len(self._pages) - 1
        return OzonPostingListResponse(result=OzonPostingListResult(postings=postings, has_next=has_next))


def test_fetch_all_postings_follows_has_next_pagination():
    page1 = [_posting(status="delivered", in_process_at="2026-09-02T00:00:00Z", sku=1, price="1", old_price=1, commission=0)]
    page2 = [_posting(status="delivered", in_process_at="2026-09-03T00:00:00Z", sku=2, price="1", old_price=1, commission=0)]
    fetch = _FakePaginatedFetch([page1, page2])

    result = _fetch_all_postings(fetch, date_from="2026-09-01T00:00:00Z", date_to="2026-09-07T23:59:59Z")

    assert len(result) == 2
    assert len(fetch.calls) == 2
    assert fetch.calls[0]["offset"] == 0
    assert fetch.calls[1]["offset"] == fetch.calls[0]["limit"]


def test_fetch_all_postings_stops_when_has_next_is_false():
    page1 = [_posting(status="delivered", in_process_at="2026-09-02T00:00:00Z", sku=1, price="1", old_price=1, commission=0)]
    fetch = _FakePaginatedFetch([page1])

    result = _fetch_all_postings(fetch, date_from="2026-09-01T00:00:00Z", date_to="2026-09-07T23:59:59Z")

    assert len(result) == 1
    assert len(fetch.calls) == 1
