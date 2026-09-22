"""Route-level tests for the full-card product AI analysis endpoints
(GET/POST .../products/{product_id}/ai-review) — role gating, store/product
isolation, and the end-to-end generate-then-list flow. Runs against the
real DemoProvider (AI_PROVIDER=demo per tests/conftest.py's env defaults),
mirroring test_advertising_ai_review_route.py's own conventions."""
from datetime import date

from tests.conftest import login


def _setup_product_with_ad_stats(db_session, store_id: str, sku: str = "555"):
    from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
    from app.models.product import Product

    product = Product(store_id=store_id, ozon_sku=sku, name="Товар для теста")
    db_session.add(product)
    db_session.flush()
    db_session.add(AdvertisingDailyStatistic(
        store_id=store_id, ozon_campaign_id="777", ozon_sku=sku, date=date(2026, 8, 1),
        spend_rub=100, impressions=1000, clicks=50, source="ozon_performance_api",
    ))
    db_session.commit()
    return product


def test_generate_requires_manager_role(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    product = _setup_product_with_ad_stats(db_session, d["store_a"].id)
    viewer = User(email="card_ai_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "card_ai_viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/products/{product.id}/ai-review/generate")
    assert resp.status_code == 403


def test_generate_for_unknown_product_returns_404(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/products/does-not-exist/ai-review/generate")
    assert resp.status_code == 404


def test_generate_without_data_returns_400_with_a_clear_message(client, db_session, two_stores_with_users):
    from app.models.product import Product

    d = two_stores_with_users
    product = Product(store_id=d["store_a"].id, ozon_sku="999", name="Пустой товар")
    db_session.add(product)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/products/{product.id}/ai-review/generate")
    assert resp.status_code == 400
    assert "Нет данных" in resp.json()["detail"]


def test_generate_then_list_end_to_end(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product = _setup_product_with_ad_stats(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")

    gen_resp = client.post(
        f"/api/stores/{d['store_a'].id}/products/{product.id}/ai-review/generate?date_from=2026-08-01&date_to=2026-08-01"
    )
    assert gen_resp.status_code == 200, gen_resp.text
    body = gen_resp.json()
    assert "ДЕМО" in body["overview"]

    list_resp = client.get(f"/api/stores/{d['store_a'].id}/products/{product.id}/ai-review")
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == body["id"]


def test_list_for_unknown_product_returns_404(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/products/does-not-exist/ai-review")
    assert resp.status_code == 404


def test_list_and_generate_are_isolated_per_store(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product_a = _setup_product_with_ad_stats(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")
    client.post(f"/api/stores/{d['store_a'].id}/products/{product_a.id}/ai-review/generate?date_from=2026-08-01&date_to=2026-08-01")

    login(client, "owner_b@example.com", "password123")
    # A product belonging to store A must not be reachable through store B's URL.
    resp = client.get(f"/api/stores/{d['store_b'].id}/products/{product_a.id}/ai-review")
    assert resp.status_code == 404
