"""Tests for the per-product funnel sync from Ozon Seller API's POST
/v1/analytics/data (app.services.product_analytics_daily_sync_service).
Fixtures use the real confirmed row shape from
backend/scripts/debug_analytics_data.py's own docstring / the real account
dump that confirmed it: dimensions=[{"id": sku, "name": ...}, {"id": day,
"name": ""}], metrics as a positional array matching _METRICS' order."""
from datetime import date

from app.services.ozon.schemas import OzonAnalyticsDataResponse, OzonAnalyticsDataResult, OzonAnalyticsDataRow, OzonAnalyticsDimensionValue
from app.services.product_analytics_daily_sync_service import _METRICS, _row_to_record


def _row(sku: str, day: str, *, revenue=0, ordered_units=0, views=0, cart_adds=0, conv=0.0, sessions=0, position=0.0) -> OzonAnalyticsDataRow:
    return OzonAnalyticsDataRow(
        dimensions=[
            OzonAnalyticsDimensionValue(id=sku, name="Товар"),
            OzonAnalyticsDimensionValue(id=day, name=""),
        ],
        metrics=[revenue, ordered_units, views, cart_adds, conv, sessions, position],
    )


def test_row_to_record_maps_positional_metrics_correctly():
    row = _row("3034472572", "2026-09-06", revenue=206500, ordered_units=35, views=1088, cart_adds=125, conv=22.12, sessions=565, position=23.13)

    record = _row_to_record(row)

    assert record["ozon_sku"] == "3034472572"
    assert record["date"] == date(2026, 9, 6)
    assert record["revenue_rub"] == 206500
    assert record["ordered_units"] == 35
    assert record["views_pdp"] == 1088
    assert record["cart_adds_pdp"] == 125
    assert record["cart_conversion_pdp_pct"] == 22.12
    assert record["sessions_pdp"] == 565
    assert record["position_category"] == 23.13


def test_row_to_record_returns_none_for_malformed_dimensions():
    row = OzonAnalyticsDataRow(dimensions=[OzonAnalyticsDimensionValue(id="111", name="x")], metrics=[0] * len(_METRICS))
    assert _row_to_record(row) is None


def test_row_to_record_returns_none_for_unparseable_date():
    row = _row("111", "not-a-date")
    assert _row_to_record(row) is None


def test_row_to_record_returns_none_for_missing_metrics():
    row = OzonAnalyticsDataRow(
        dimensions=[OzonAnalyticsDimensionValue(id="111", name="x"), OzonAnalyticsDimensionValue(id="2026-09-01", name="")],
        metrics=[1, 2],
    )
    assert _row_to_record(row) is None


class _FakeClient:
    def __init__(self, rows: list[OzonAnalyticsDataRow]):
        self._rows = rows
        self.calls: list[dict] = []

    def get_analytics_data(self, **kwargs):
        self.calls.append(kwargs)
        return OzonAnalyticsDataResponse(result=OzonAnalyticsDataResult(data=self._rows, totals=[]), timestamp="x")


def test_sync_creates_and_updates_rows(db_session, two_stores_with_users):
    from app.models.product_analytics_daily_statistic import ProductAnalyticsDailyStatistic
    from app.services.product_analytics_daily_sync_service import sync_product_analytics_daily_statistics

    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient([_row("111", "2026-09-01", revenue=1000, ordered_units=2, views=50, cart_adds=5, conv=10.0, sessions=40, position=3.5)])

    outcome = sync_product_analytics_daily_statistics(db_session, store_id=store_id, client=client, date_from=date(2026, 9, 1), date_to=date(2026, 9, 1))

    assert outcome.fetched == 1
    assert outcome.created == 1
    assert outcome.updated == 0
    row = db_session.query(ProductAnalyticsDailyStatistic).filter(ProductAnalyticsDailyStatistic.store_id == store_id).one()
    assert row.ozon_sku == "111"
    assert row.revenue_rub == 1000
    assert row.views_pdp == 50

    # Re-sync with a changed value must update the same row, not duplicate it.
    client2 = _FakeClient([_row("111", "2026-09-01", revenue=1500, ordered_units=3, views=60, cart_adds=6, conv=10.0, sessions=45, position=3.0)])
    outcome2 = sync_product_analytics_daily_statistics(db_session, store_id=store_id, client=client2, date_from=date(2026, 9, 1), date_to=date(2026, 9, 1))
    assert outcome2.created == 0
    assert outcome2.updated == 1
    rows = db_session.query(ProductAnalyticsDailyStatistic).filter(ProductAnalyticsDailyStatistic.store_id == store_id).all()
    assert len(rows) == 1
    assert rows[0].revenue_rub == 1500
    assert rows[0].views_pdp == 60


def test_sync_uses_fixed_metrics_order_and_dimension(db_session, two_stores_with_users):
    from app.services.product_analytics_daily_sync_service import sync_product_analytics_daily_statistics

    d = two_stores_with_users
    client = _FakeClient([])

    sync_product_analytics_daily_statistics(db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 9, 1), date_to=date(2026, 9, 7))

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["dimension"] == ["sku", "day"]
    assert call["metrics"] == _METRICS
    assert call["date_from"] == "2026-09-01"
    assert call["date_to"] == "2026-09-07"
    assert call["limit"] == 1000
    assert call["offset"] == 0


def test_sync_records_error_on_ozon_api_error(db_session, two_stores_with_users):
    from app.services.ozon.exceptions import OzonAPIError
    from app.services.product_analytics_daily_sync_service import sync_product_analytics_daily_statistics

    class _FailingClient:
        def get_analytics_data(self, **kwargs):
            raise OzonAPIError("Требуется подписка Premium Plus")

    d = two_stores_with_users
    outcome = sync_product_analytics_daily_statistics(db_session, store_id=d["store_a"].id, client=_FailingClient())

    assert outcome.fetched == 0
    assert outcome.created == 0
    assert len(outcome.errors) == 1
    assert "Premium Plus" in outcome.errors[0]


def test_sync_store_isolation(db_session, two_stores_with_users):
    from app.models.product_analytics_daily_statistic import ProductAnalyticsDailyStatistic
    from app.services.product_analytics_daily_sync_service import sync_product_analytics_daily_statistics

    d = two_stores_with_users
    client = _FakeClient([_row("111", "2026-09-01", revenue=100, ordered_units=1)])
    sync_product_analytics_daily_statistics(db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 9, 1), date_to=date(2026, 9, 1))

    store_b_rows = db_session.query(ProductAnalyticsDailyStatistic).filter(ProductAnalyticsDailyStatistic.store_id == d["store_b"].id).all()
    assert store_b_rows == []
