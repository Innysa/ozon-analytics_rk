"""Tests for the cash-flow-statement sync from Ozon Seller API's POST
/v1/finance/cash-flow-statement/list (app.services.cash_flow_statement_
sync_service). Fixtures use the real confirmed response shape from two
rounds of real diagnostic output (2026-09-10) — see
app.models.cash_flow_statement_period.CashFlowStatementPeriod's own
docstring for the full confirmed contract, most importantly: details[] is
matched to cash_flows[] by period.begin/period.end (NOT array position),
and "services" is a mixed bucket (storage + ads-per-click + insurance),
never split into Хранение/Штрафы."""
from datetime import date

from app.services.cash_flow_statement_sync_service import _parse_ozon_ts, sync_cash_flow_statement_periods


def _flow(begin: str, end: str, *, orders=0, returns=0, commission=0, services=0, item_delivery_and_return=0) -> dict:
    return {
        "period": {"id": 0, "begin": begin, "end": end},
        "orders_amount": orders,
        "returns_amount": returns,
        "commission_amount": commission,
        "services_amount": services,
        "item_delivery_and_return_amount": item_delivery_and_return,
        "currency_code": "RUB",
    }


def _detail(begin: str, end: str, *, delivery_services_total=0, delivery_services_items=None,
            return_total=0, return_items=None, services_total=0, services_items=None) -> dict:
    return {
        "period": {"id": 0, "begin": begin, "end": end},
        "begin_balance_amount": 7546644.61,
        "payments": [{"payment": -1811395.24, "currency_code": "RUB"}],
        "delivery": {
            "total": 1137907.3,
            "amount": 1259092.6,
            "delivery_services": {"total": delivery_services_total, "items": delivery_services_items or []},
            "return": {"total": return_total, "items": return_items or []},
        },
        "loan": 0,
        "invoice_transfer": 15717.64,
        "rfbs": {"total": 0, "transfer_delivery": 0, "transfer_delivery_return": 0, "compensation_delivery_return": 0, "partial_compensation": 0, "partial_compensation_return": 0},
        "services": {"total": services_total, "items": services_items or []},
    }


def test_parse_ozon_ts_handles_the_confirmed_timestamp_format():
    assert _parse_ozon_ts("2026-09-07T00:00:00Z") == date(2026, 9, 7)
    assert _parse_ozon_ts(None) is None
    assert _parse_ozon_ts("not-a-date") is None


class _FakeClient:
    def __init__(self, flows: list[dict], details: list[dict], *, page_count: int = 1):
        self._flows = flows
        self._details = details
        self._page_count = page_count
        self.calls: list[dict] = []

    def get_cash_flow_statement(self, **kwargs):
        self.calls.append(kwargs)
        # Simulate real pagination: only return data on page 1.
        if kwargs["page"] == 1:
            return {"result": {"cash_flows": self._flows, "page_count": self._page_count, "details": self._details}}
        return {"result": {"cash_flows": [], "page_count": self._page_count, "details": []}}


def test_sync_matches_details_to_cash_flows_by_period_not_position(db_session, two_stores_with_users):
    """The critical, previously-open question this session resolved against
    a real account: details[] entries carry their OWN period, and must be
    matched by begin/end — NOT by array index. This test deliberately puts
    them in a DIFFERENT order than cash_flows[] to catch a regression to
    positional matching."""
    from app.models.cash_flow_statement_period import CashFlowStatementPeriod

    d = two_stores_with_users
    store_id = d["store_a"].id

    flows = [
        _flow("2026-08-31T00:00:00Z", "2026-09-06T00:00:00Z", orders=5000, commission=-500),
        _flow("2026-08-24T00:00:00Z", "2026-08-30T00:00:00Z", orders=6000, commission=-600),
    ]
    # Details in the OPPOSITE order from flows.
    details = [
        _detail("2026-08-24T00:00:00Z", "2026-08-30T00:00:00Z", delivery_services_total=-100, services_total=-200),
        _detail("2026-08-31T00:00:00Z", "2026-09-06T00:00:00Z", delivery_services_total=-300, services_total=-400),
    ]
    client = _FakeClient(flows, details)

    outcome = sync_cash_flow_statement_periods(db_session, store_id=store_id, client=client, date_from=date(2026, 8, 24), date_to=date(2026, 9, 6))

    assert outcome.fetched == 2
    assert outcome.created == 2

    row_sep = db_session.query(CashFlowStatementPeriod).filter(
        CashFlowStatementPeriod.store_id == store_id, CashFlowStatementPeriod.period_begin == date(2026, 8, 31)
    ).one()
    assert float(row_sep.delivery_services_total) == -300
    assert float(row_sep.services_total) == -400
    assert float(row_sep.orders_amount) == 5000

    row_aug = db_session.query(CashFlowStatementPeriod).filter(
        CashFlowStatementPeriod.store_id == store_id, CashFlowStatementPeriod.period_begin == date(2026, 8, 24)
    ).one()
    assert float(row_aug.delivery_services_total) == -100
    assert float(row_aug.services_total) == -200
    assert float(row_aug.orders_amount) == 6000


def test_sync_stores_items_as_json_and_updates_existing_period(db_session, two_stores_with_users):
    from app.models.cash_flow_statement_period import CashFlowStatementPeriod

    d = two_stores_with_users
    store_id = d["store_a"].id
    items = [{"name": "MarketplaceServiceItemDirectFlowLogisticSum", "price": -112007.9}]
    flows = [_flow("2026-09-07T00:00:00Z", "2026-09-13T00:00:00Z", orders=1000)]
    details = [_detail("2026-09-07T00:00:00Z", "2026-09-13T00:00:00Z", delivery_services_total=-112007.9, delivery_services_items=items)]

    outcome = sync_cash_flow_statement_periods(db_session, store_id=store_id, client=_FakeClient(flows, details), date_from=date(2026, 9, 7), date_to=date(2026, 9, 13))
    assert outcome.created == 1

    row = db_session.query(CashFlowStatementPeriod).filter(CashFlowStatementPeriod.store_id == store_id).one()
    assert float(row.delivery_services_total) == -112007.9
    assert "MarketplaceServiceItemDirectFlowLogisticSum" in row.delivery_services_items_json

    # Re-sync with an updated figure must update the same row, not duplicate it.
    flows2 = [_flow("2026-09-07T00:00:00Z", "2026-09-13T00:00:00Z", orders=1500)]
    details2 = [_detail("2026-09-07T00:00:00Z", "2026-09-13T00:00:00Z", delivery_services_total=-99999)]
    outcome2 = sync_cash_flow_statement_periods(db_session, store_id=store_id, client=_FakeClient(flows2, details2), date_from=date(2026, 9, 7), date_to=date(2026, 9, 13))
    assert outcome2.created == 0
    assert outcome2.updated == 1
    rows = db_session.query(CashFlowStatementPeriod).filter(CashFlowStatementPeriod.store_id == store_id).all()
    assert len(rows) == 1
    assert float(rows[0].delivery_services_total) == -99999
    assert float(rows[0].orders_amount) == 1500


def test_sync_records_error_on_ozon_api_error(db_session, two_stores_with_users):
    from app.services.ozon.exceptions import OzonAPIError

    class _FailingClient:
        def get_cash_flow_statement(self, **kwargs):
            raise OzonAPIError("Ozon вернул ошибку 400")

    d = two_stores_with_users
    outcome = sync_cash_flow_statement_periods(db_session, store_id=d["store_a"].id, client=_FailingClient())

    assert outcome.fetched == 0
    assert outcome.created == 0
    assert len(outcome.errors) == 1


def test_sync_paginates_when_page_count_greater_than_one(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id

    class _PagedClient:
        def __init__(self):
            self.calls: list[int] = []

        def get_cash_flow_statement(self, **kwargs):
            self.calls.append(kwargs["page"])
            if kwargs["page"] == 1:
                return {"result": {"cash_flows": [_flow("2026-09-07T00:00:00Z", "2026-09-13T00:00:00Z", orders=1)], "page_count": 2, "details": []}}
            return {"result": {"cash_flows": [_flow("2026-08-31T00:00:00Z", "2026-09-06T00:00:00Z", orders=2)], "page_count": 2, "details": []}}

    client = _PagedClient()
    outcome = sync_cash_flow_statement_periods(db_session, store_id=store_id, client=client, date_from=date(2026, 8, 31), date_to=date(2026, 9, 13))

    assert client.calls == [1, 2]
    assert outcome.fetched == 2
    assert outcome.created == 2


def test_sync_store_isolation(db_session, two_stores_with_users):
    from app.models.cash_flow_statement_period import CashFlowStatementPeriod

    d = two_stores_with_users
    flows = [_flow("2026-09-07T00:00:00Z", "2026-09-13T00:00:00Z", orders=100)]
    sync_cash_flow_statement_periods(db_session, store_id=d["store_a"].id, client=_FakeClient(flows, []), date_from=date(2026, 9, 7), date_to=date(2026, 9, 13))

    store_b_rows = db_session.query(CashFlowStatementPeriod).filter(CashFlowStatementPeriod.store_id == d["store_b"].id).all()
    assert store_b_rows == []
