"""Tests for the store-wide daily dashboard (app.services.dashboard_service,
GET /api/stores/{id}/dashboard) — the at-a-glance summary combining three
independent, already-existing data sources (product-card CSV import, both
advertising sources, reviews), each with its own has_data."""
from datetime import date, datetime, timezone

import pytest

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
    assert body["inventory"]["has_data"] is False
    assert body["logistics"]["has_data"] is False


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


def test_orders_revenue_prefers_ozon_seller_api_over_csv_when_both_present(client, db_session, two_stores_with_users):
    from app.models.order_daily_statistic import OrderDailyStatistic
    from app.models.product_card_statistic import ProductCardStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    # CSV data would show orders=10/revenue=10000 for the current period —
    # the auto source below must win instead, not be summed with it.
    db_session.add(ProductCardStatistic(
        store_id=store_id, ozon_sku="111", date=date(2026, 8, 25),
        ordered_units=10, ordered_sum_actual_price_rub=10000, source="csv_import",
    ))
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 8, 26), delivery_schema="FBO",
        ordered_units=3, ordered_sum_rub=3600, ordered_sum_discounted_rub=3300,
        delivered_units=3, delivered_sum_rub=3300,
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=-150,
        source="ozon_seller_api",
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
    assert block["source"] == "ozon_seller_api"
    assert block["orders"]["current"] == 3
    assert block["revenue_rub"]["current"] == 3300


def test_orders_revenue_falls_back_to_csv_when_no_auto_order_data(client, db_session, two_stores_with_users):
    from app.models.product_card_statistic import ProductCardStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(ProductCardStatistic(
        store_id=store_id, ozon_sku="111", date=date(2026, 8, 25),
        ordered_units=10, ordered_sum_actual_price_rub=10000, source="csv_import",
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
    assert block["source"] == "csv_import"
    assert block["orders"]["current"] == 10


def test_advertising_share_uses_ozon_seller_api_revenue_when_that_is_the_active_source(client, db_session, two_stores_with_users):
    from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
    from app.models.order_daily_statistic import OrderDailyStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 8, 25), delivery_schema="FBO",
        ordered_units=1, ordered_sum_rub=5000, ordered_sum_discounted_rub=5000,
        delivered_units=1, delivered_sum_rub=5000,
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=-500,
        source="ozon_seller_api",
    ))
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
    body = resp.json()
    assert body["orders_revenue"]["source"] == "ozon_seller_api"
    # 500 / 5000 * 100 = 10%
    assert body["advertising"]["spend_share_of_revenue_pct"] == 10.0


def test_orders_revenue_buyout_pct_from_ozon_seller_api_source(client, db_session, two_stores_with_users):
    from app.models.order_daily_statistic import OrderDailyStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 8, 25), delivery_schema="FBO",
        ordered_units=10, ordered_sum_rub=12000, ordered_sum_discounted_rub=12000,
        delivered_units=8, delivered_sum_rub=9600,
        cancelled_units=2, cancelled_sum_rub=2400, unfinished_units=0, commission_rub=-1000,
        source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-21", "date_to": "2026-08-30"},
    )
    assert resp.status_code == 200
    block = resp.json()["orders_revenue"]
    # 8 / 10 * 100 = 80%
    assert block["buyout_pct"] == 80.0


def test_orders_revenue_buyout_pct_from_csv_source(client, db_session, two_stores_with_users):
    from app.models.product_card_statistic import ProductCardStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(ProductCardStatistic(
        store_id=store_id, ozon_sku="111", date=date(2026, 8, 25),
        ordered_units=20, ordered_sum_actual_price_rub=20000, bought_out_units=15, source="csv_import",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-21", "date_to": "2026-08-30"},
    )
    assert resp.status_code == 200
    block = resp.json()["orders_revenue"]
    # 15 / 20 * 100 = 75%
    assert block["buyout_pct"] == 75.0


def test_inventory_block_sums_fbo_and_fbs_stock_excluding_archived(client, db_session, two_stores_with_users):
    from app.models.product import Product

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(Product(store_id=store_id, ozon_sku="111", name="Товар 1", fbo_stock=10, fbs_stock=5))
    db_session.add(Product(store_id=store_id, ozon_sku="222", name="Товар 2", fbo_stock=3, fbs_stock=None))
    db_session.add(Product(store_id=store_id, ozon_sku="333", name="Архивный", fbo_stock=1000, fbs_stock=1000, is_archived=True))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/dashboard")
    assert resp.status_code == 200
    block = resp.json()["inventory"]
    assert block["has_data"] is True
    assert block["fbo_units"] == 13
    assert block["fbs_units"] == 5
    assert block["total_units"] == 18


def test_inventory_block_no_data_when_catalog_never_synced(client, db_session, two_stores_with_users):
    from app.models.product import Product

    d = two_stores_with_users
    store_id = d["store_a"].id
    # A product exists (e.g. created manually for cost_price_rub) but the
    # catalog sync that would populate fbo_stock/fbs_stock never ran.
    db_session.add(Product(store_id=store_id, ozon_sku="111", name="Товар без синка остатков"))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/dashboard")
    assert resp.status_code == 200
    assert resp.json()["inventory"]["has_data"] is False


def test_logistics_block_sums_periods_fully_contained_in_range(client, db_session, two_stores_with_users):
    from app.models.cash_flow_statement_period import CashFlowStatementPeriod

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(CashFlowStatementPeriod(
        store_id=store_id, period_begin=date(2026, 8, 17), period_end=date(2026, 8, 23),
        orders_amount=5000, returns_amount=0, commission_amount=0, services_amount=0,
        item_delivery_and_return_amount=0, currency_code="RUB",
        delivery_services_total=-100, delivery_return_total=-20, services_total=-50, source="ozon_seller_api",
    ))
    db_session.add(CashFlowStatementPeriod(
        store_id=store_id, period_begin=date(2026, 8, 24), period_end=date(2026, 8, 30),
        orders_amount=5000, returns_amount=0, commission_amount=0, services_amount=0,
        item_delivery_and_return_amount=0, currency_code="RUB",
        delivery_services_total=-200, delivery_return_total=-30, services_total=-60, source="ozon_seller_api",
    ))
    # This period straddles the requested range's edge — must NOT be summed
    # (same "fully contained only" rule as AdvertisingStatistic).
    db_session.add(CashFlowStatementPeriod(
        store_id=store_id, period_begin=date(2026, 8, 31), period_end=date(2026, 9, 6),
        orders_amount=5000, returns_amount=0, commission_amount=0, services_amount=0,
        item_delivery_and_return_amount=0, currency_code="RUB",
        delivery_services_total=-999, delivery_return_total=-999, services_total=-999, source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-17", "date_to": "2026-08-30"},
    )
    assert resp.status_code == 200
    block = resp.json()["logistics"]
    assert block["has_data"] is True
    assert block["periods_summed"] == 2
    assert block["logistics_rub"] == -300
    assert block["returns_logistics_rub"] == -50
    assert block["other_services_rub"] == -110
    # No items recorded for these periods — fines/storage stay at 0 rather
    # than silently dropping the -110 from services_total.
    assert block["fines_rub"] == 0
    assert block["storage_rub"] == 0


def test_logistics_block_splits_fines_and_storage_out_of_services_items(client, db_session, two_stores_with_users):
    """Confirmed 2026-09-10 (real item names, not guessed): a fine
    (FinesShipmentNonRecommendedSlot) and a storage fee
    (MarketplaceServiceItemTemporaryStorageRedistribution) both live inside
    services.items[] — the Дашборд must pull them into their own figures
    rather than leaving them buried in one "Прочие услуги" lump."""
    import json

    from app.models.cash_flow_statement_period import CashFlowStatementPeriod

    d = two_stores_with_users
    store_id = d["store_a"].id
    services_items = [
        {"name": "MarketplaceServiceItemTemporaryStorageRedistribution", "price": -1404},
        {"name": "MarketplaceServiceCostPerClick", "price": -81971.47},
        {"name": "FinesShipmentNonRecommendedSlot", "price": -504},
    ]
    others_items = [
        {"name": "MarketplaceRedistributionOfAcquiringOperation", "price": -23694.81},
        {"name": "MarketplaceSellerDecompensationItemByTypeDocOperation", "price": -3020.95},
    ]
    db_session.add(CashFlowStatementPeriod(
        store_id=store_id, period_begin=date(2026, 8, 17), period_end=date(2026, 8, 23),
        orders_amount=5000, returns_amount=0, commission_amount=0, services_amount=0,
        item_delivery_and_return_amount=0, currency_code="RUB",
        services_total=-83879.47, services_items_json=json.dumps(services_items),
        others_total=-26715.76, others_items_json=json.dumps(others_items),
        source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-17", "date_to": "2026-08-23"},
    )
    assert resp.status_code == 200
    block = resp.json()["logistics"]
    assert block["fines_rub"] == -504
    assert block["storage_rub"] == -1404
    assert block["other_services_rub"] == -81971.47  # remainder: -83879.47 - (-504) - (-1404)
    assert block["other_deductions_rub"] == -26715.76
    assert block["other_services_top_item_name"] == "MarketplaceServiceCostPerClick"
    assert block["other_services_top_item_rub"] == -81971.47


def test_logistics_block_surfaces_largest_uncategorized_item_by_magnitude(client, db_session, two_stores_with_users):
    """Confirmed 2026-09-10: a real account found a volatile line
    (MarketplaceServiseItemAgencyFeeForSale — Ozon's own typo) ranging from
    -2 787 193.76 to +8 764 167.68 across different weeks, once dominating
    "Прочие услуги" enough to look like a sync bug. The Дашборд must surface
    the single largest (by |price|) uncategorized item across all summed
    periods — here across TWO periods, to also confirm it looks across all
    of them, not just one."""
    import json

    from app.models.cash_flow_statement_period import CashFlowStatementPeriod

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(CashFlowStatementPeriod(
        store_id=store_id, period_begin=date(2026, 7, 27), period_end=date(2026, 8, 2),
        orders_amount=1000, returns_amount=0, commission_amount=0, services_amount=5643060.15,
        item_delivery_and_return_amount=0, currency_code="RUB",
        services_total=5621663.17,
        services_items_json=json.dumps([
            {"name": "MarketplaceServiseItemAgencyFeeForSale", "price": 4908815.64},
            {"name": "MarketplaceServiceCostPerClick", "price": -50000},
        ]),
        source="ozon_seller_api",
    ))
    db_session.add(CashFlowStatementPeriod(
        store_id=store_id, period_begin=date(2026, 8, 3), period_end=date(2026, 8, 9),
        orders_amount=1000, returns_amount=0, commission_amount=0, services_amount=-100000,
        item_delivery_and_return_amount=0, currency_code="RUB",
        services_total=-100000,
        services_items_json=json.dumps([{"name": "InsuranceService", "price": -100000}]),
        source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-07-27", "date_to": "2026-08-09"},
    )
    assert resp.status_code == 200
    block = resp.json()["logistics"]
    assert block["other_services_top_item_name"] == "MarketplaceServiseItemAgencyFeeForSale"
    assert block["other_services_top_item_rub"] == 4908815.64


def test_logistics_block_top_item_none_when_no_uncategorized_items(client, db_session, two_stores_with_users):
    from app.models.cash_flow_statement_period import CashFlowStatementPeriod

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(CashFlowStatementPeriod(
        store_id=store_id, period_begin=date(2026, 8, 17), period_end=date(2026, 8, 23),
        orders_amount=0, returns_amount=0, commission_amount=0, services_amount=0,
        item_delivery_and_return_amount=0, currency_code="RUB",
        services_total=0, source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-17", "date_to": "2026-08-23"},
    )
    assert resp.status_code == 200
    block = resp.json()["logistics"]
    assert block["other_services_top_item_name"] is None
    assert block["other_services_top_item_rub"] is None


def test_logistics_block_prorates_a_period_only_partially_in_range(client, db_session, two_stores_with_users):
    """Regression for a real account (2026-09-12): a range not aligned to
    Ozon's own weekly periods used to drop a straddling period ENTIRELY
    (periods_summed=0, logistics_rub=None) even though real money was
    genuinely spent inside the requested range — a 12-day range once
    showed less than half the true "Логистика и услуги" total with no
    warning. A partially-overlapping period must now be included, scaled
    by the fraction of its own days that fall in range, and flagged
    is_estimated so the number reads as approximate, not exact."""
    from app.models.cash_flow_statement_period import CashFlowStatementPeriod

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(CashFlowStatementPeriod(
        store_id=store_id, period_begin=date(2026, 8, 17), period_end=date(2026, 8, 23),
        orders_amount=0, returns_amount=0, commission_amount=0, services_amount=0,
        item_delivery_and_return_amount=0, currency_code="RUB",
        delivery_services_total=-100, source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    # A short range that only partially overlaps the one stored 7-day
    # period (2 of its 7 days: 08-20 and 08-21).
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-20", "date_to": "2026-08-21"},
    )
    assert resp.status_code == 200
    block = resp.json()["logistics"]
    assert block["has_data"] is True
    assert block["periods_summed"] == 1
    assert block["is_estimated"] is True
    # -100 * (2 overlapping days / 7 period days)
    assert block["logistics_rub"] == -28.57


def test_logistics_block_zero_periods_when_range_has_no_overlap_at_all(client, db_session, two_stores_with_users):
    from app.models.cash_flow_statement_period import CashFlowStatementPeriod

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(CashFlowStatementPeriod(
        store_id=store_id, period_begin=date(2026, 8, 17), period_end=date(2026, 8, 23),
        orders_amount=0, returns_amount=0, commission_amount=0, services_amount=0,
        item_delivery_and_return_amount=0, currency_code="RUB",
        delivery_services_total=-100, source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    # A range with NO overlap with the one stored period at all.
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-09-01", "date_to": "2026-09-05"},
    )
    assert resp.status_code == 200
    block = resp.json()["logistics"]
    assert block["has_data"] is True
    assert block["periods_summed"] == 0
    assert block["logistics_rub"] is None


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


def test_advertising_block_surfaces_cash_flow_only_ad_formats(client, db_session, two_stores_with_users):
    """Regression for a real account (2026-09-12): AdvertisingDailyStatistic
    (Performance API) never captures CPO-style "оплата за заказ" promotions
    or seller bonus mailings at all — confirmed by matching exact rub
    amounts between a manually exported Ozon "Начисления" report and
    cash-flow's own services_items_json. spend_other_formats_rub must pull
    these out (sign-flipped to positive, matching spend_auto_rub's
    convention) WITHOUT double-counting MarketplaceServiceCostPerClick,
    which spend_auto_rub already covers."""
    import json

    from app.models.cash_flow_statement_period import CashFlowStatementPeriod

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(CashFlowStatementPeriod(
        store_id=store_id, period_begin=date(2026, 8, 17), period_end=date(2026, 8, 23),
        orders_amount=0, returns_amount=0, commission_amount=0, services_amount=0,
        item_delivery_and_return_amount=0, currency_code="RUB",
        services_total=-500,
        services_items_json=json.dumps([
            {"name": "MarketplaceServiceCostPerClick", "price": -300},
            {"name": "MarketplaceServicePromotionWithCostPerOrder", "price": -150},
            {"name": "MarketplaceServiceItemElectronicServicesPremiumSellerBonusAccrual", "price": -50},
        ]),
        source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-17", "date_to": "2026-08-23"},
    )
    assert resp.status_code == 200
    block = resp.json()["advertising"]
    assert block["has_data"] is True
    assert block["spend_other_formats_rub"]["current"] == 200  # 150 + 50, NOT the 300 CostPerClick


def test_manual_ad_spend_straddling_previous_window_does_not_fabricate_delta_pct(client, db_session, two_stores_with_users):
    """Regression for a real Дашборд bug (2026-09-11): a manually uploaded
    AdvertisingStatistic row spanning most of August straddled the
    auto-computed "previous period" boundary, got excluded by
    _sum_manual_ad_spend's fully-contained rule, and left a tiny leftover
    total that made a normal current-period spend look like a "+1070%"
    jump. previous must come back None (no delta_pct/direction) whenever
    the previous window has a straddling row — not a fabricated small
    number — while the current period's own figure is untouched."""
    from app.models.advertising_statistic import AdvertisingStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    # Straddles the previous window (2026-08-21..2026-08-30) on both ends —
    # real spend, but not fully contained in either half.
    db_session.add(AdvertisingStatistic(
        store_id=store_id, ozon_sku="111", ozon_campaign_id="1",
        period_start=date(2026, 8, 1), period_end=date(2026, 8, 25),
        spend_rub=100000, source="csv_import",
    ))
    # A small row that IS fully contained in the previous window — this is
    # the misleading "real but not the real total" leftover.
    db_session.add(AdvertisingStatistic(
        store_id=store_id, ozon_sku="111", ozon_campaign_id="1",
        period_start=date(2026, 8, 26), period_end=date(2026, 8, 30),
        spend_rub=100, source="csv_import",
    ))
    # Current period's own upload, fully contained — a normal figure.
    db_session.add(AdvertisingStatistic(
        store_id=store_id, ozon_sku="111", ozon_campaign_id="1",
        period_start=date(2026, 8, 31), period_end=date(2026, 9, 9),
        spend_rub=1200, source="csv_import",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-31", "date_to": "2026-09-09"},
    )
    assert resp.status_code == 200
    block = resp.json()["advertising"]
    metric = block["spend_manual_rub"]
    assert metric["current"] == 1200
    assert metric["previous"] is None
    assert metric["delta_pct"] is None
    assert metric["direction"] is None


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


def test_margin_block_computes_only_when_cost_price_known_for_every_delivered_unit(client, db_session, two_stores_with_users):
    from app.models.order_daily_statistic import OrderDailyStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 8, 25), delivery_schema="FBO",
        ordered_units=1, ordered_sum_rub=1200, ordered_sum_discounted_rub=1200,
        delivered_units=1, delivered_sum_rub=1200,
        cost_of_delivered_rub=400, cost_of_delivered_known_units=1,
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=-100,
        source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-21", "date_to": "2026-08-30"},
    )
    assert resp.status_code == 200
    block = resp.json()["margin"]
    assert block["has_data"] is True
    assert block["cost_known"] is True
    # 1200 (delivered) + (-100) (commission) - 400 (cost) - 0 (ad spend) = 700
    assert block["margin_rub"] == 700.0
    assert block["margin_pct"] == pytest.approx(58.33, abs=0.01)


def test_margin_block_is_null_when_cost_price_missing_for_some_units(client, db_session, two_stores_with_users):
    from app.models.order_daily_statistic import OrderDailyStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 8, 25), delivery_schema="FBO",
        ordered_units=2, ordered_sum_rub=2400, ordered_sum_discounted_rub=2400,
        delivered_units=2, delivered_sum_rub=2400,
        cost_of_delivered_rub=400, cost_of_delivered_known_units=1,  # only 1 of 2 delivered units has a known cost
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=-200,
        source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/dashboard",
        params={"date_from": "2026-08-21", "date_to": "2026-08-30"},
    )
    assert resp.status_code == 200
    block = resp.json()["margin"]
    assert block["has_data"] is True
    assert block["cost_known"] is False
    assert block["margin_rub"] is None
    assert block["cost_of_delivered_rub"] is None  # partial cost figure withheld, not shown as if it were complete


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
