"""Tests for the per-product advertising detail (product detail page's
"Реклама" tab): the same auto-collected AdvertisingDailyStatistic source as
the campaign detail's auto_daily, sliced by SKU across every campaign that
advertised it instead of by one campaign across every SKU it covers."""
from datetime import date

import pytest

from tests.conftest import login


def _make_campaign(db_session, store_id: str, *, ozon_campaign_id: str, name: str, state: str = "CAMPAIGN_STATE_RUNNING"):
    from app.models.advertising_campaign import AdvertisingCampaign

    campaign = AdvertisingCampaign(
        store_id=store_id, ozon_campaign_id=ozon_campaign_id, name=name, campaign_type="SKU", state=state,
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


def _add_row(db_session, *, store_id: str, ozon_campaign_id: str, ozon_sku: str, day: date, spend: float, revenue: float, impressions: int, clicks: int, orders: int = 0):
    from app.models.advertising_daily_statistic import AdvertisingDailyStatistic

    row = AdvertisingDailyStatistic(
        store_id=store_id, ozon_campaign_id=ozon_campaign_id, ozon_sku=ozon_sku, date=day,
        spend_rub=spend, revenue_rub=revenue, impressions=impressions, clicks=clicks, orders=orders,
        source="ozon_performance_api",
    )
    db_session.add(row)
    return row


def test_no_data_for_sku_returns_has_data_false(db_session, two_stores_with_users):
    from app.services.advertising_analytics_service import compute_product_advertising_auto_daily

    d = two_stores_with_users
    result = compute_product_advertising_auto_daily(db_session, store_id=d["store_a"].id, ozon_sku="does-not-exist")
    assert result.has_data is False
    assert result.by_campaign == []


def test_two_campaigns_same_sku_are_broken_down_separately(db_session, two_stores_with_users):
    from app.services.advertising_analytics_service import compute_product_advertising_auto_daily

    d = two_stores_with_users
    _make_campaign(db_session, d["store_a"].id, ozon_campaign_id="c1", name="Кампания 1")
    _make_campaign(db_session, d["store_a"].id, ozon_campaign_id="c2", name="Кампания 2")
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="c1", ozon_sku="111", day=date(2026, 9, 1), spend=100, revenue=500, impressions=1000, clicks=20, orders=2)
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="c2", ozon_sku="111", day=date(2026, 9, 1), spend=50, revenue=200, impressions=400, clicks=10, orders=1)
    # A different SKU on campaign 1 must not leak into this product's totals.
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="c1", ozon_sku="999", day=date(2026, 9, 1), spend=9999, revenue=9999, impressions=9999, clicks=99)
    db_session.flush()

    result = compute_product_advertising_auto_daily(db_session, store_id=d["store_a"].id, ozon_sku="111")
    assert result.has_data is True
    assert result.total_spend_rub == pytest.approx(150)
    assert result.total_revenue_rub == pytest.approx(700)
    assert result.total_orders == 3

    by_campaign = {b.campaign_id: b for b in result.by_campaign}
    assert set(by_campaign) == {"c1", "c2"}
    assert by_campaign["c1"].campaign_name == "Кампания 1"
    assert by_campaign["c1"].spend_rub == pytest.approx(100)
    assert by_campaign["c1"].campaign_state == "CAMPAIGN_STATE_RUNNING"
    assert by_campaign["c2"].spend_rub == pytest.approx(50)
    # Sorted by spend descending.
    assert result.by_campaign[0].campaign_id == "c1"


def test_unsynced_campaign_falls_back_to_ozon_campaign_id_as_name(db_session, two_stores_with_users):
    """AdvertisingDailyStatistic can exist for a campaign whose metadata was
    never synced into AdvertisingCampaign — must not crash, just show the id."""
    from app.services.advertising_analytics_service import compute_product_advertising_auto_daily

    d = two_stores_with_users
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="unsynced-42", ozon_sku="111", day=date(2026, 9, 1), spend=10, revenue=20, impressions=100, clicks=5)
    db_session.flush()

    result = compute_product_advertising_auto_daily(db_session, store_id=d["store_a"].id, ozon_sku="111")
    assert result.has_data is True
    assert result.by_campaign[0].campaign_name == "unsynced-42"
    assert result.by_campaign[0].campaign_state is None


def test_two_days_produce_day_over_day_comparison(db_session, two_stores_with_users):
    from app.services.advertising_analytics_service import compute_product_advertising_auto_daily

    d = two_stores_with_users
    _make_campaign(db_session, d["store_a"].id, ozon_campaign_id="c1", name="Кампания 1")
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="c1", ozon_sku="111", day=date(2026, 9, 1), spend=100, revenue=500, impressions=1000, clicks=20)
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="c1", ozon_sku="111", day=date(2026, 9, 2), spend=150, revenue=400, impressions=900, clicks=25)
    db_session.flush()

    result = compute_product_advertising_auto_daily(db_session, store_id=d["store_a"].id, ozon_sku="111")
    comp = result.daily_comparison
    assert comp is not None
    assert comp.date_today == date(2026, 9, 2)
    assert comp.spend_rub.direction == "up"
    assert comp.revenue_rub.direction == "down"


def test_route_end_to_end_and_store_isolation(client, db_session, two_stores_with_users):
    from app.models.product import Product

    d = two_stores_with_users
    product = Product(store_id=d["store_a"].id, ozon_sku="111", name="Тестовый товар")
    db_session.add(product)
    db_session.flush()
    _make_campaign(db_session, d["store_a"].id, ozon_campaign_id="c1", name="Кампания 1")
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="c1", ozon_sku="111", day=date(2026, 9, 1), spend=100, revenue=500, impressions=1000, clicks=20, orders=1)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/advertising/product-auto-daily?product_id={product.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] is True
    assert body["by_campaign"][0]["campaign_name"] == "Кампания 1"

    # owner_a has no membership on store B at all, so this is rejected before
    # the product lookup even runs (require_store_role's own 403) — a
    # membership boundary check, not our 404-for-cross-store-product-id one.
    forbidden = client.get(f"/api/stores/{d['store_b'].id}/advertising/product-auto-daily?product_id={product.id}")
    assert forbidden.status_code == 403

    # The platform admin DOES have access to both stores — this exercises
    # the actual product-not-in-this-store 404 the route itself raises.
    login(client, "admin@example.com", "adminpass123")
    not_found = client.get(f"/api/stores/{d['store_b'].id}/advertising/product-auto-daily?product_id={product.id}")
    assert not_found.status_code == 404


def test_daily_rows_only_that_product_and_campaign(db_session, two_stores_with_users):
    """The expanded-row detail for one campaign on the "Реклама" tab must be
    scoped to exactly (sku, campaign) — neither another SKU on the same
    campaign nor the same SKU on another campaign should leak in."""
    from app.services.advertising_analytics_service import compute_product_campaign_daily_rows

    d = two_stores_with_users
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="c1", ozon_sku="111", day=date(2026, 9, 1), spend=100, revenue=500, impressions=1000, clicks=20, orders=2)
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="c1", ozon_sku="111", day=date(2026, 9, 2), spend=150, revenue=400, impressions=900, clicks=25, orders=1)
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="c1", ozon_sku="999", day=date(2026, 9, 1), spend=9999, revenue=9999, impressions=9999, clicks=99)
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="c2", ozon_sku="111", day=date(2026, 9, 1), spend=8888, revenue=8888, impressions=8888, clicks=88)
    db_session.flush()

    rows = compute_product_campaign_daily_rows(db_session, store_id=d["store_a"].id, ozon_sku="111", ozon_campaign_id="c1")

    assert len(rows) == 2
    # Sorted by date descending.
    assert rows[0].date == date(2026, 9, 2)
    assert rows[0].spend_rub == pytest.approx(150)
    assert rows[0].revenue_rub == pytest.approx(400)
    assert rows[0].orders == 1
    assert rows[0].drr_calculated_pct == pytest.approx(150 / 400 * 100, rel=1e-3)
    assert rows[1].date == date(2026, 9, 1)
    assert rows[1].spend_rub == pytest.approx(100)


def test_daily_rows_empty_when_no_match(db_session, two_stores_with_users):
    from app.services.advertising_analytics_service import compute_product_campaign_daily_rows

    d = two_stores_with_users
    rows = compute_product_campaign_daily_rows(db_session, store_id=d["store_a"].id, ozon_sku="does-not-exist", ozon_campaign_id="c1")
    assert rows == []


def test_daily_rows_route_end_to_end_and_store_isolation(client, db_session, two_stores_with_users):
    from app.models.product import Product

    d = two_stores_with_users
    product = Product(store_id=d["store_a"].id, ozon_sku="111", name="Тестовый товар")
    db_session.add(product)
    db_session.flush()
    _make_campaign(db_session, d["store_a"].id, ozon_campaign_id="c1", name="Кампания 1")
    _add_row(db_session, store_id=d["store_a"].id, ozon_campaign_id="c1", ozon_sku="111", day=date(2026, 9, 1), spend=100, revenue=500, impressions=1000, clicks=20, orders=1)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{d['store_a'].id}/advertising/product-campaign-daily",
        params={"product_id": product.id, "ozon_campaign_id": "c1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["date"] == "2026-09-01"
    assert body["items"][0]["spend_rub"] == 100.0

    login(client, "admin@example.com", "adminpass123")
    not_found = client.get(
        f"/api/stores/{d['store_b'].id}/advertising/product-campaign-daily",
        params={"product_id": product.id, "ozon_campaign_id": "c1"},
    )
    assert not_found.status_code == 404
