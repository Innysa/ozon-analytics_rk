"""Tests for app.services.accrual_daily_sync_service — syncing Ozon's true
daily accrual total (POST /v1/finance/accrual/by-day). No real HTTP: a tiny
duck-typed fake client stands in for OzonSellerClient, same convention as
every other sync service's tests in this project."""
import json
from datetime import date

from app.models.accrual_daily_statistic import AccrualDailyStatistic
from app.services.accrual_daily_sync_service import (
    _extract_commission_ozon_rub,
    _extract_realization_revenue,
    _find_record_list,
    sync_accrual_daily_statistic,
    sync_recent_accrual_days,
)
from app.services.ozon.exceptions import OzonAPIError


class _FakeClient:
    def __init__(self, *, pages=None, raise_exc=None, realization_data=None, realization_raise_exc=None):
        # pages: list of page responses (dicts), returned in order per call
        self._pages = pages or []
        self._raise_exc = raise_exc
        self._realization_data = realization_data if realization_data is not None else {"rows": []}
        self._realization_raise_exc = realization_raise_exc
        self.calls: list[tuple[str, int, int]] = []
        self.realization_calls: list[tuple[int, int, int]] = []

    def get_accrual_by_day(self, *, day: str, page: int, page_size: int):
        self.calls.append((day, page, page_size))
        if self._raise_exc:
            raise self._raise_exc
        idx = page - 1
        if idx < len(self._pages):
            return self._pages[idx]
        return {"result": {"records": []}}

    def get_realization_by_day(self, *, year: int, month: int, day: int):
        self.realization_calls.append((year, month, day))
        if self._realization_raise_exc:
            raise self._realization_raise_exc
        return self._realization_data


def test_find_record_list_ignores_unrelated_empty_arrays():
    data = {"result": {"page_count": [], "records": [{"accrual_id": "1"}]}}
    assert _find_record_list(data) == [{"accrual_id": "1"}]


def test_find_record_list_returns_none_when_nothing_list_shaped_present():
    assert _find_record_list({"result": {"records": []}}) is None
    assert _find_record_list({"result": {}}) is None


def test_extract_commission_ozon_rub_sums_across_all_products_in_posting():
    record = {
        "posting": {
            "products": [
                {"commission": {"sale_commission": {"amount": "-10.50"}}},
                {"commission": {"sale_commission": {"amount": "-5.25"}}},
            ],
        },
    }
    assert _extract_commission_ozon_rub(record) == -15.75


def test_extract_commission_ozon_rub_returns_zero_when_no_posting():
    """NON_ITEM charges (e.g. advertising) aren't tied to a specific
    shipment — no posting/products is expected, not an error."""
    assert _extract_commission_ozon_rub({"accrual_id": "x"}) == 0.0
    assert _extract_commission_ozon_rub({"posting": {}}) == 0.0
    assert _extract_commission_ozon_rub({"posting": {"products": []}}) == 0.0


def test_extract_realization_revenue_weighs_price_by_quantity_not_row_count():
    """CONFIRMED 2026-09-19 against a real account, two different days:
    seller_price_per_instance is a PER-UNIT price — the row's own unit
    count lives separately under delivery_commission.quantity /
    return_commission.quantity, so a plain sum of prices across rows
    (ignoring quantity) undercounts revenue on any row with more than
    one unit."""
    data = {
        "rows": [
            {"seller_price_per_instance": 1000.0, "delivery_commission": {"quantity": 2}},
            {"seller_price_per_instance": 500.0, "delivery_commission": {"quantity": 1}, "return_commission": {"quantity": 1}},
        ]
    }
    sales, returns = _extract_realization_revenue(data)
    assert sales == 2500.0  # 1000*2 + 500*1
    assert returns == -500.0  # -(500*1)


def test_extract_realization_revenue_handles_rows_with_no_return():
    data = {"rows": [{"seller_price_per_instance": 100.0, "delivery_commission": {"quantity": 3}}]}
    sales, returns = _extract_realization_revenue(data)
    assert sales == 300.0
    assert returns == 0.0


def test_sync_accrual_daily_statistic_creates_new_row(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient(
        pages=[{"result": {"records": [
            {
                "accrual_id": "a1", "total_amount": {"amount": "100.50"}, "accrued_category": "POSTING",
                "posting": {"products": [{"commission": {"sale_commission": {"amount": "-30.00"}}}]},
            },
            {"accrual_id": "a2", "total_amount": {"amount": "-20.00"}, "accrued_category": "NON_ITEM"},
        ]}}],
        realization_data={"rows": [{"seller_price_per_instance": 200.0, "delivery_commission": {"quantity": 1}}]},
    )

    outcome = sync_accrual_daily_statistic(db_session, store_id=store_id, client=client, day=date(2026, 9, 12))

    assert outcome.fetched is True
    assert outcome.created is True
    assert outcome.record_count == 2
    assert outcome.total_amount_rub == 80.50
    assert outcome.error is None
    assert outcome.realization_fetched is True
    assert outcome.realization_error is None
    assert client.realization_calls == [(2026, 9, 12)]

    row = db_session.query(AccrualDailyStatistic).filter(
        AccrualDailyStatistic.store_id == store_id, AccrualDailyStatistic.date == date(2026, 9, 12),
    ).one()
    assert float(row.total_amount_rub) == 80.50
    by_category = json.loads(row.by_category_json)
    assert by_category == {"POSTING": 100.50, "NON_ITEM": -20.00}
    assert row.record_count == 2
    assert row.source == "ozon_seller_api"
    assert float(row.commission_ozon_rub) == -30.00
    assert float(row.sales_rub) == 200.0
    assert float(row.returns_rub) == 0.0


def test_sync_accrual_daily_statistic_realization_failure_does_not_block_accrual_sync(db_session, two_stores_with_users):
    """realization/by-day is a SEPARATE Ozon method from accrual/by-day —
    a failure fetching it must not roll back or block the accrual figures,
    which are independently useful (and were already working before
    realization/by-day was ever added)."""
    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient(
        pages=[{"result": {"records": [
            {"accrual_id": "a1", "total_amount": {"amount": "50.00"}, "accrued_category": "ITEM"},
        ]}}],
        realization_raise_exc=OzonAPIError("Ozon вернул ошибку сервера 500"),
    )

    outcome = sync_accrual_daily_statistic(db_session, store_id=store_id, client=client, day=date(2026, 9, 12))

    assert outcome.fetched is True
    assert outcome.total_amount_rub == 50.00
    assert outcome.realization_fetched is False
    assert "500" in outcome.realization_error

    row = db_session.query(AccrualDailyStatistic).filter(
        AccrualDailyStatistic.store_id == store_id, AccrualDailyStatistic.date == date(2026, 9, 12),
    ).one()
    assert float(row.total_amount_rub) == 50.00
    assert float(row.sales_rub) == 0.0  # default — realization never succeeded for this row


def test_sync_accrual_daily_statistic_updates_existing_row(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(AccrualDailyStatistic(
        store_id=store_id, date=date(2026, 9, 12), total_amount_rub=1.0,
        by_category_json="{}", record_count=0, source="ozon_seller_api",
    ))
    db_session.commit()

    client = _FakeClient(pages=[{"result": {"records": [
        {"accrual_id": "a1", "total_amount": {"amount": "50.00"}, "accrued_category": "ITEM"},
    ]}}])
    outcome = sync_accrual_daily_statistic(db_session, store_id=store_id, client=client, day=date(2026, 9, 12))

    assert outcome.fetched is True
    assert outcome.created is False
    rows = db_session.query(AccrualDailyStatistic).filter(
        AccrualDailyStatistic.store_id == store_id, AccrualDailyStatistic.date == date(2026, 9, 12),
    ).all()
    assert len(rows) == 1  # upserted, not duplicated
    assert float(rows[0].total_amount_rub) == 50.00


def test_sync_accrual_daily_statistic_paginates_until_a_short_page(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    from app.services import accrual_daily_sync_service as svc

    full_page = [{"accrual_id": f"a{i}", "total_amount": {"amount": "1.00"}, "accrued_category": "ITEM"} for i in range(svc.PAGE_SIZE)]
    short_page = [{"accrual_id": "last", "total_amount": {"amount": "2.00"}, "accrued_category": "ITEM"}]
    client = _FakeClient(pages=[{"result": {"records": full_page}}, {"result": {"records": short_page}}])

    outcome = sync_accrual_daily_statistic(db_session, store_id=store_id, client=client, day=date(2026, 9, 12))

    assert outcome.record_count == svc.PAGE_SIZE + 1
    assert client.calls == [("2026-09-12", 1, svc.PAGE_SIZE), ("2026-09-12", 2, svc.PAGE_SIZE)]


def test_sync_accrual_daily_statistic_stops_if_ozon_ignores_page_and_repeats(db_session, two_stores_with_users):
    """DEFENSIVE regression test: if Ozon ever ignored the `page` param and
    kept returning the same PAGE_SIZE-long page (never confirmed to happen,
    but never confirmed NOT to either — no real day tested so far exceeded
    PAGE_SIZE), the old pagination loop would re-append the identical
    records up to MAX_PAGES, wildly inflating totals. Deduping by
    accrual_id must stop this instead of looping forever."""
    from app.services import accrual_daily_sync_service as svc

    d = two_stores_with_users
    store_id = d["store_a"].id
    repeated_page = [{"accrual_id": f"a{i}", "total_amount": {"amount": "1.00"}, "accrued_category": "ITEM"} for i in range(svc.PAGE_SIZE)]
    client = _FakeClient(pages=[{"result": {"records": repeated_page}}] * 5)  # same page returned every time

    outcome = sync_accrual_daily_statistic(db_session, store_id=store_id, client=client, day=date(2026, 9, 12))

    assert outcome.record_count == svc.PAGE_SIZE  # NOT PAGE_SIZE * 5
    assert outcome.total_amount_rub == svc.PAGE_SIZE * 1.00
    assert len(client.calls) == 2  # page 1 (full), page 2 (all duplicates -> stop)


def test_sync_accrual_daily_statistic_handles_a_day_with_zero_records(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient(pages=[{"result": {"records": []}}])

    outcome = sync_accrual_daily_statistic(db_session, store_id=store_id, client=client, day=date(2026, 9, 12))

    assert outcome.fetched is True
    assert outcome.record_count == 0
    assert outcome.total_amount_rub == 0.0


def test_sync_accrual_daily_statistic_surfaces_real_errors(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient(raise_exc=OzonAPIError("Ozon вернул ошибку сервера 500"))

    outcome = sync_accrual_daily_statistic(db_session, store_id=store_id, client=client, day=date(2026, 9, 12))

    assert outcome.fetched is False
    assert "500" in outcome.error
    assert db_session.query(AccrualDailyStatistic).count() == 0


def test_sync_recent_accrual_days_never_includes_today(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient(pages=[{"result": {"records": []}}])

    results = sync_recent_accrual_days(
        db_session, store_id=store_id, client=client, days=3, today=date(2026, 9, 14),
    )

    assert [day for day, _ in results] == [date(2026, 9, 13), date(2026, 9, 12), date(2026, 9, 11)]


def test_sync_recent_accrual_days_refetches_even_already_archived_days(db_session, two_stores_with_users):
    """Unlike the realization-report backfill, this must NOT skip an
    already-archived day — Ozon can revise a recent day's accruals after
    the fact, so every day inside the trailing window is re-fetched."""
    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(AccrualDailyStatistic(
        store_id=store_id, date=date(2026, 9, 13), total_amount_rub=1.0,
        by_category_json="{}", record_count=0, source="ozon_seller_api",
    ))
    db_session.commit()

    client = _FakeClient(pages=[{"result": {"records": [
        {"accrual_id": "a1", "total_amount": {"amount": "99.00"}, "accrued_category": "ITEM"},
    ]}}])
    sync_recent_accrual_days(db_session, store_id=store_id, client=client, days=1, today=date(2026, 9, 14))

    assert ("2026-09-13", 1, 1000) in client.calls
    row = db_session.query(AccrualDailyStatistic).filter(
        AccrualDailyStatistic.store_id == store_id, AccrualDailyStatistic.date == date(2026, 9, 13),
    ).one()
    assert float(row.total_amount_rub) == 99.00
