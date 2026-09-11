"""Tests for «РНП Товары» — the per-product monthly planner
(app.services.product_planner_service, app.api.routes.product_planner).
Uses dates relative to "today" so the forecast/elapsed-days logic is
exercised for a fully-past month (forecast == actual), the current month
(elapsed_days == today.day), and a fully-future month (no forecast)."""
from datetime import date, datetime, timezone

from tests.conftest import login

TODAY = datetime.now(timezone.utc).date()
PAST_YEAR, PAST_MONTH = (TODAY.year, TODAY.month - 1) if TODAY.month > 1 else (TODAY.year - 1, 12)
CUR_YEAR, CUR_MONTH = TODAY.year, TODAY.month
FUTURE_YEAR, FUTURE_MONTH = (TODAY.year, TODAY.month + 1) if TODAY.month < 12 else (TODAY.year + 1, 1)


def _seed_product(db_session, store_id, *, sku="SKU-PLAN-1", name="Товар", cost_price_rub=None, fbo_stock=None, fbs_stock=None):
    from app.models.product import Product

    product = Product(
        store_id=store_id, ozon_sku=sku, name=name,
        cost_price_rub=cost_price_rub, fbo_stock=fbo_stock, fbs_stock=fbs_stock,
    )
    db_session.add(product)
    db_session.commit()
    return product


def _seed_order_stat(db_session, store_id, sku, day, *, ordered_units, ordered_sum_rub, delivered_units, delivered_sum_rub):
    from app.models.product_order_daily_statistic import ProductOrderDailyStatistic

    db_session.add(ProductOrderDailyStatistic(
        store_id=store_id, ozon_sku=sku, date=day, delivery_schema="FBO",
        ordered_units=ordered_units, ordered_sum_rub=ordered_sum_rub, ordered_sum_discounted_rub=ordered_sum_rub,
        delivered_units=delivered_units, delivered_sum_rub=delivered_sum_rub,
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=0,
        source="ozon_seller_api",
    ))


def _seed_ad_spend(db_session, store_id, sku, day, *, campaign_id="camp-1", spend_rub):
    from app.models.advertising_daily_statistic import AdvertisingDailyStatistic

    db_session.add(AdvertisingDailyStatistic(
        store_id=store_id, ozon_campaign_id=campaign_id, ozon_sku=sku, date=day, spend_rub=spend_rub,
        source="ozon_performance_api",
    ))


def test_no_products_returns_has_data_false(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/product-planner", params={"year": CUR_YEAR, "month": CUR_MONTH})
    assert resp.status_code == 200
    assert resp.json()["has_data"] is False


def test_past_month_forecast_equals_actual(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, cost_price_rub=100)
    _seed_order_stat(
        db_session, store_id, "SKU-PLAN-1", date(PAST_YEAR, PAST_MONTH, 1),
        ordered_units=10, ordered_sum_rub=2000, delivered_units=8, delivered_sum_rub=1600,
    )
    _seed_ad_spend(db_session, store_id, "SKU-PLAN-1", date(PAST_YEAR, PAST_MONTH, 1), spend_rub=100)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/product-planner", params={"year": PAST_YEAR, "month": PAST_MONTH})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] is True
    row = body["rows"][0]
    assert row["buyouts"]["actual_month_rub"] == 1600
    assert row["buyouts"]["forecast_month_rub"] == row["buyouts"]["actual_month_rub"]
    assert row["orders"]["forecast_month_units"] == row["orders"]["actual_month_units"] == 10

    # Прибыль до ДРР = 1600 - 100*8 = 800; Прибыль с ДРР = 800 - 100 = 700
    assert row["profit"]["actual_month_rub"] == 700
    assert row["margin_before_ad_pct"] == round(800 / 1600 * 100, 2)
    assert row["margin_after_ad_pct"] == round(700 / 1600 * 100, 2)
    # КРПП = Прибыль_с_ДРР / Прибыль_до_ДРР
    assert row["krpp_pct"] == round(700 / 800 * 100, 2)


def test_future_month_has_no_forecast(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/product-planner", params={"year": FUTURE_YEAR, "month": FUTURE_MONTH})
    assert resp.status_code == 200
    body = resp.json()
    assert body["elapsed_days"] == 0
    row = body["rows"][0]
    assert row["orders"]["forecast_month_rub"] is None
    assert row["orders"]["forecast_month_units"] is None


def test_current_month_elapsed_days_matches_today(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/product-planner", params={"year": CUR_YEAR, "month": CUR_MONTH})
    assert resp.status_code == 200
    assert resp.json()["elapsed_days"] == TODAY.day


def test_cost_unknown_leaves_profit_and_margins_none(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, cost_price_rub=None)
    _seed_order_stat(
        db_session, store_id, "SKU-PLAN-1", date(PAST_YEAR, PAST_MONTH, 1),
        ordered_units=5, ordered_sum_rub=500, delivered_units=5, delivered_sum_rub=500,
    )
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/product-planner", params={"year": PAST_YEAR, "month": PAST_MONTH})
    row = resp.json()["rows"][0]
    assert row["cost_known"] is False
    assert row["profit"]["actual_month_rub"] is None
    assert row["krpp_pct"] is None
    assert row["margin_before_ad_pct"] is None
    assert row["margin_after_ad_pct"] is None


def test_stock_and_days_of_stock_remaining(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, fbo_stock=100, fbs_stock=50)
    # 30 units delivered over PAST_MONTH's first day only — pace uses
    # elapsed_days for a past month, which is the full days_in_month.
    _seed_order_stat(
        db_session, store_id, "SKU-PLAN-1", date(PAST_YEAR, PAST_MONTH, 1),
        ordered_units=30, ordered_sum_rub=3000, delivered_units=30, delivered_sum_rub=3000,
    )
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/product-planner", params={"year": PAST_YEAR, "month": PAST_MONTH})
    row = resp.json()["rows"][0]
    assert row["stock_total_units"] == 150
    assert row["stock_fbo_units"] == 100
    assert row["stock_fbs_units"] == 50
    assert row["days_of_stock_remaining"] is not None and row["days_of_stock_remaining"] > 0


def test_no_stock_data_leaves_days_of_stock_remaining_none(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, fbo_stock=None, fbs_stock=None)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/product-planner", params={"year": PAST_YEAR, "month": PAST_MONTH})
    row = resp.json()["rows"][0]
    assert row["stock_total_units"] is None
    assert row["days_of_stock_remaining"] is None


def test_localization_is_always_none(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/product-planner", params={"year": PAST_YEAR, "month": PAST_MONTH})
    assert resp.json()["rows"][0]["localization_pct"] is None
    assert resp.json()["total"]["localization_pct"] is None


def test_set_plan_persists_and_is_reflected_in_response(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    product = _seed_product(db_session, store_id)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.put(
        f"/api/stores/{store_id}/product-planner/products/{product.id}/plan",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
        json={"plan_orders_units": 100, "plan_orders_sum_rub": 10000, "plan_ad_budget_pct": 5},
    )
    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["orders"]["plan_month_units"] == 100
    assert row["orders"]["plan_month_rub"] == 10000
    assert row["ad_budget"]["plan_pct"] == 5
    assert row["ad_budget"]["plan_month_rub"] == 500  # derived: 5% of orders plan (10000)
    days_in_month = resp.json()["days_in_month"]
    assert row["orders"]["plan_day_rub"] == round(10000 / days_in_month, 2)

    from app.models.product_monthly_plan import ProductMonthlyPlan
    rows = db_session.query(ProductMonthlyPlan).filter(ProductMonthlyPlan.product_id == product.id).all()
    assert len(rows) == 1  # second call below must UPDATE, not duplicate

    resp2 = client.put(
        f"/api/stores/{store_id}/product-planner/products/{product.id}/plan",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
        json={"plan_orders_units": 200},
    )
    assert resp2.json()["rows"][0]["orders"]["plan_month_units"] == 200
    rows2 = db_session.query(ProductMonthlyPlan).filter(ProductMonthlyPlan.product_id == product.id).all()
    assert len(rows2) == 1


def test_buyouts_and_profit_never_have_a_plan(client, db_session, two_stores_with_users):
    """Confirmed 2026-09-11: plan is only ever entered for Заказы/Рекламный
    бюджет — Выкупы/Прибыль must never show plan_day_rub/plan_month_rub (or
    unit equivalents), even after saving a plan for the other two groups."""
    d = two_stores_with_users
    store_id = d["store_a"].id
    product = _seed_product(db_session, store_id)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.put(
        f"/api/stores/{store_id}/product-planner/products/{product.id}/plan",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
        json={"plan_orders_units": 100, "plan_orders_sum_rub": 10000, "plan_ad_budget_pct": 5},
    )
    row = resp.json()["rows"][0]
    for metric_key in ("buyouts", "profit"):
        assert row[metric_key]["plan_day_rub"] is None
        assert row[metric_key]["plan_month_rub"] is None
        assert row[metric_key]["plan_day_units"] is None
        assert row[metric_key]["plan_month_units"] is None


def test_bulk_set_plans_saves_multiple_products_in_one_call(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    p1 = _seed_product(db_session, store_id, sku="SKU-BULK-1", name="Товар 1")
    p2 = _seed_product(db_session, store_id, sku="SKU-BULK-2", name="Товар 2")
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.put(
        f"/api/stores/{store_id}/product-planner/plans/bulk",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
        json={"entries": [
            {"product_id": p1.id, "plan_orders_units": 100, "plan_orders_sum_rub": 10000, "plan_ad_budget_pct": 5},
            {"product_id": p2.id, "plan_orders_units": 50, "plan_orders_sum_rub": 5000, "plan_ad_budget_pct": 5},
        ]},
    )
    assert resp.status_code == 200
    rows_by_id = {r["product_id"]: r for r in resp.json()["rows"]}
    assert rows_by_id[p1.id]["orders"]["plan_month_units"] == 100
    assert rows_by_id[p1.id]["ad_budget"]["plan_pct"] == 5
    assert rows_by_id[p1.id]["ad_budget"]["plan_month_rub"] == 500
    assert rows_by_id[p2.id]["orders"]["plan_month_units"] == 50
    assert rows_by_id[p2.id]["ad_budget"]["plan_month_rub"] == 250

    from app.models.product_monthly_plan import ProductMonthlyPlan
    assert db_session.query(ProductMonthlyPlan).filter(ProductMonthlyPlan.store_id == store_id).count() == 2


def test_bulk_set_plans_updates_not_duplicates_on_second_call(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    product = _seed_product(db_session, store_id)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    for units in (100, 200):
        client.put(
            f"/api/stores/{store_id}/product-planner/plans/bulk",
            params={"year": CUR_YEAR, "month": CUR_MONTH},
            json={"entries": [{"product_id": product.id, "plan_orders_units": units}]},
        )

    from app.models.product_monthly_plan import ProductMonthlyPlan
    rows = db_session.query(ProductMonthlyPlan).filter(ProductMonthlyPlan.product_id == product.id).all()
    assert len(rows) == 1
    assert rows[0].plan_orders_units == 200


def test_bulk_set_plans_skips_products_from_other_stores(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product_a = _seed_product(db_session, d["store_a"].id, sku="SKU-A-OWN")
    product_b = _seed_product(db_session, d["store_b"].id, sku="SKU-B-FOREIGN")
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.put(
        f"/api/stores/{d['store_a'].id}/product-planner/plans/bulk",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
        json={"entries": [
            {"product_id": product_a.id, "plan_orders_units": 10},
            {"product_id": product_b.id, "plan_orders_units": 999},
        ]},
    )
    assert resp.status_code == 200

    from app.models.product_monthly_plan import ProductMonthlyPlan
    assert db_session.query(ProductMonthlyPlan).filter(ProductMonthlyPlan.product_id == product_b.id).count() == 0
    assert db_session.query(ProductMonthlyPlan).filter(ProductMonthlyPlan.product_id == product_a.id).count() == 1


def test_bulk_set_plans_requires_manager_role(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    store_id = d["store_a"].id
    product = _seed_product(db_session, store_id)
    viewer = User(email="viewer_bulk@example.com", password_hash=hash_password("password123"), full_name="Viewer")
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=store_id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "viewer_bulk@example.com", "password123")
    resp = client.put(
        f"/api/stores/{store_id}/product-planner/plans/bulk",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
        json={"entries": [{"product_id": product.id, "plan_orders_units": 10}]},
    )
    assert resp.status_code == 403


def test_set_plan_requires_manager_role(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    store_id = d["store_a"].id
    product = _seed_product(db_session, store_id)
    viewer = User(email="viewer_planner@example.com", password_hash=hash_password("password123"), full_name="Viewer")
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=store_id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "viewer_planner@example.com", "password123")
    resp = client.put(
        f"/api/stores/{store_id}/product-planner/products/{product.id}/plan",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
        json={"plan_orders_units": 100},
    )
    assert resp.status_code == 403


def test_set_plan_store_isolation(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product_b = _seed_product(db_session, d["store_b"].id)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.put(
        f"/api/stores/{d['store_a'].id}/product-planner/products/{product_b.id}/plan",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
        json={"plan_orders_units": 100},
    )
    assert resp.status_code == 404


def test_suggest_plan_averages_history_and_never_persists(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    product = _seed_product(db_session, store_id, cost_price_rub=50)
    # Two months of history immediately before the current month.
    m1_year, m1_month = (CUR_YEAR, CUR_MONTH - 1) if CUR_MONTH > 1 else (CUR_YEAR - 1, 12)
    m2_year, m2_month = (m1_year, m1_month - 1) if m1_month > 1 else (m1_year - 1, 12)
    _seed_order_stat(db_session, store_id, "SKU-PLAN-1", date(m1_year, m1_month, 1), ordered_units=10, ordered_sum_rub=1000, delivered_units=10, delivered_sum_rub=1000)
    _seed_order_stat(db_session, store_id, "SKU-PLAN-1", date(m2_year, m2_month, 1), ordered_units=20, ordered_sum_rub=2000, delivered_units=20, delivered_sum_rub=2000)
    _seed_ad_spend(db_session, store_id, "SKU-PLAN-1", date(m1_year, m1_month, 1), spend_rub=10)  # 10/1000 = 1%
    _seed_ad_spend(db_session, store_id, "SKU-PLAN-1", date(m2_year, m2_month, 1), spend_rub=100)  # 100/2000 = 5%
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/product-planner/products/{product.id}/suggest-plan",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["based_on_months"] == 2
    assert body["suggested_orders_units"] == 15  # average of 10 and 20
    # Weighted: total ad spend (110) / total orders sum (3000) * 100 = 3.67%
    # — NOT a naive average of each month's own ratio (1% and 5% -> 3%),
    # which would let a low-revenue month's noisy ratio skew the result.
    assert body["suggested_ad_budget_pct"] == round(110 / 3000 * 100, 2)

    from app.models.product_monthly_plan import ProductMonthlyPlan
    assert db_session.query(ProductMonthlyPlan).filter(ProductMonthlyPlan.product_id == product.id).count() == 0


def test_suggest_plan_no_history_returns_zero_months(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    product = _seed_product(db_session, store_id)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(
        f"/api/stores/{store_id}/product-planner/products/{product.id}/suggest-plan",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
    )
    body = resp.json()
    assert body["based_on_months"] == 0
    assert body["suggested_orders_units"] is None


def test_total_row_sums_across_products(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, sku="SKU-PLAN-1", name="Товар 1", cost_price_rub=10)
    _seed_product(db_session, store_id, sku="SKU-PLAN-2", name="Товар 2", cost_price_rub=20)
    _seed_order_stat(db_session, store_id, "SKU-PLAN-1", date(PAST_YEAR, PAST_MONTH, 1), ordered_units=5, ordered_sum_rub=500, delivered_units=5, delivered_sum_rub=500)
    _seed_order_stat(db_session, store_id, "SKU-PLAN-2", date(PAST_YEAR, PAST_MONTH, 1), ordered_units=3, ordered_sum_rub=300, delivered_units=3, delivered_sum_rub=300)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/product-planner", params={"year": PAST_YEAR, "month": PAST_MONTH})
    body = resp.json()
    assert len(body["rows"]) == 2
    assert body["total"]["orders"]["actual_month_units"] == 8
    assert body["total"]["orders"]["actual_month_rub"] == 800


def test_total_row_ad_budget_plan_is_weighted_average_pct(client, db_session, two_stores_with_users):
    """The "Итого" row's ad_budget.plan_pct must be a weighted average
    (total planned ad rub / total planned orders rub), not a naive average
    of each product's own %, which would let a low-revenue product's plan
    skew the total disproportionately."""
    d = two_stores_with_users
    store_id = d["store_a"].id
    p1 = _seed_product(db_session, store_id, sku="SKU-TOT-1", name="Товар 1")
    p2 = _seed_product(db_session, store_id, sku="SKU-TOT-2", name="Товар 2")
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    client.put(
        f"/api/stores/{store_id}/product-planner/products/{p1.id}/plan",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
        json={"plan_orders_sum_rub": 9000, "plan_ad_budget_pct": 1},  # -> 90 ₽ planned ad spend
    )
    client.put(
        f"/api/stores/{store_id}/product-planner/products/{p2.id}/plan",
        params={"year": CUR_YEAR, "month": CUR_MONTH},
        json={"plan_orders_sum_rub": 1000, "plan_ad_budget_pct": 10},  # -> 100 ₽ planned ad spend
    )

    resp = client.get(f"/api/stores/{store_id}/product-planner", params={"year": CUR_YEAR, "month": CUR_MONTH})
    total = resp.json()["total"]
    # Naive average of 1% and 10% would be 5.5% — weighted is (90+100)/(9000+1000)*100 = 1.9%.
    assert total["ad_budget"]["plan_pct"] == round(190 / 10000 * 100, 2)
    assert total["ad_budget"]["plan_month_rub"] == 190


def test_product_planner_store_isolation(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    _seed_product(db_session, d["store_b"].id, sku="SKU-B-ONLY")
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/product-planner", params={"year": CUR_YEAR, "month": CUR_MONTH})
    assert resp.json()["has_data"] is False
