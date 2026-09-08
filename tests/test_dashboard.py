"""Tests for the store-wide daily dashboard (app.services.dashboard_service,
GET /api/stores/{id}/dashboard) — the at-a-glance summary combining three
independent, already-existing data sources (product-card CSV import, both
advertising sources, reviews), each with its own has_data."""
from datetime import date, datetime, timezone

from tests.conftest import login


def test_no_data_anywhere_returns_all_blocks_empty(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.get(f"/api/stores/{d['store_a'].id}/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["orders_revenue"]["has_data"] is False
    assert body["advertising"]["has_data"] is False
    assert body["reviews"]["has_data"] is False


def test_orders_revenue_block_compares_to_previous_equal_period(client, db_session, two_stores_with_users):
    from app.models.product_card_statistic import ProductCardStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    # Explicit 10-day period so "previous period" is unambiguous: current
    # 2026-08-21..2026-08-30, previous 2026-08-11..2026-08-20.
    db_session.add(ProductCardStatistic(
        store_id=store_id, ozon_sku="111", date=date(2026, 8, 25),
        ordered_units=10, ordered_sum_actual_price_rub=10000, source="csv_import",
    ))
    db_session.add(ProductCardStatistic(
        store_id=store_id, ozon_sku="111", date=date(2026, 8, 15),
        ordered_units=4, ordered_sum_actual_price_rub=4000, source="csv_import",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-21", "date_to": "2026-08-30"},
    )
    assert resp.status_code == 200
    block = resp.json()["orders_revenue"]
    assert block["has_data"] is True
    assert block["orders"]["current"] == 10
    assert block["orders"]["previous"] == 4
    assert block["orders"]["direction"] == "up"
    assert block["revenue_rub"]["current"] == 10000
    assert block["avg_order_value_rub"] == 1000.0


def test_advertising_block_keeps_auto_and_manual_spend_separate(client, db_session, two_stores_with_users):
    from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
    from app.models.advertising_statistic import AdvertisingStatistic
    from app.models.product_card_statistic import ProductCardStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(ProductCardStatistic(
        store_id=store_id, ozon_sku="111", date=date(2026, 8, 25),
        ordered_units=10, ordered_sum_actual_price_rub=10000, source="csv_import",
    ))
    db_session.add(AdvertisingDailyStatistic(
        store_id=store_id, ozon_campaign_id="1", ozon_sku="111", date=date(2026, 8, 25),
        spend_rub=500, source="ozon_performance_api",
    ))
    db_session.add(AdvertisingStatistic(
        store_id=store_id, ozon_sku="111", ozon_campaign_id="1",
        period_start=date(2026, 8, 22), period_end=date(2026, 8, 22),
        spend_rub=500, source="csv_import",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-21", "date_to": "2026-08-30"},
    )
    assert resp.status_code == 200
    block = resp.json()["advertising"]
    assert block["has_data"] is True
    assert block["spend_auto_rub"]["current"] == 500
    assert block["spend_manual_rub"]["current"] == 500
    # (500 + 500) / 10000 * 100 = 10%
    assert block["spend_share_of_revenue_pct"] == 10.0


def test_advertising_block_without_revenue_has_no_share_pct(client, db_session, two_stores_with_users):
    from app.models.advertising_daily_statistic import AdvertisingDailyStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(AdvertisingDailyStatistic(
        store_id=store_id, ozon_campaign_id="1", ozon_sku="111", date=date(2026, 8, 25),
        spend_rub=500, source="ozon_performance_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-21", "date_to": "2026-08-30"},
    )
    assert resp.status_code == 200
    block = resp.json()["advertising"]
    assert block["has_data"] is True
    assert block["spend_manual_rub"] is None
    assert block["spend_share_of_revenue_pct"] is None


def test_reviews_block_counts_new_reviews_in_period_and_backlog(client, db_session, two_stores_with_users):
    from app.models.review import Review, ReviewSource, ReviewStatus

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(Review(
        store_id=store_id, ozon_review_id="r1", source=ReviewSource.CSV_IMPORT, rating=5,
        status=ReviewStatus.NEW, published_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
    ))
    db_session.add(Review(
        store_id=store_id, ozon_review_id="r2", source=ReviewSource.CSV_IMPORT, rating=1,
        status=ReviewStatus.NEW, published_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-21", "date_to": "2026-08-30"},
    )
    assert resp.status_code == 200
    block = resp.json()["reviews"]
    assert block["has_data"] is True
    assert block["new_count"]["current"] == 1
    assert block["new_count"]["previous"] == 1
    assert block["avg_rating_current"] == 5.0
    assert block["without_reply_count"] == 2


def test_store_isolation(client, db_session, two_stores_with_users):
    from app.models.product_card_statistic import ProductCardStatistic

    d = two_stores_with_users
    db_session.add(ProductCardStatistic(
        store_id=d["store_a"].id, ozon_sku="111", date=date(2026, 8, 25),
        ordered_units=10, ordered_sum_actual_price_rub=10000, source="csv_import",
    ))
    db_session.commit()

    login(client, "owner_b@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_b'].id}/dashboard")
    assert resp.status_code == 200
    assert resp.json()["orders_revenue"]["has_data"] is False

    forbidden = client.get(f"/api/stores/{d['store_a'].id}/dashboard")
    assert forbidden.status_code == 403
