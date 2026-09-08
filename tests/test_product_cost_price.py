"""Tests for PUT .../products/{id}/cost-price — себестоимость, entered
manually since Ozon's API never exposes a seller's own purchase cost (see
app/api/routes/products.py's docstring on that endpoint)."""
from tests.conftest import login


def _make_product(db_session, store_id, ozon_sku="1001"):
    from app.models.product import Product

    product = Product(store_id=store_id, ozon_sku=ozon_sku, name="Товар")
    db_session.add(product)
    db_session.commit()
    return product


def test_manager_can_set_cost_price(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")

    resp = client.put(f"/api/stores/{d['store_a'].id}/products/{product.id}/cost-price", json={"cost_price_rub": "450.50"})
    assert resp.status_code == 200
    assert resp.json()["cost_price_rub"] == "450.50"

    get_resp = client.get(f"/api/stores/{d['store_a'].id}/products/{product.id}")
    assert get_resp.json()["cost_price_rub"] == "450.50"


def test_can_clear_cost_price_with_null(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")
    client.put(f"/api/stores/{d['store_a'].id}/products/{product.id}/cost-price", json={"cost_price_rub": "100"})

    resp = client.put(f"/api/stores/{d['store_a'].id}/products/{product.id}/cost-price", json={"cost_price_rub": None})
    assert resp.status_code == 200
    assert resp.json()["cost_price_rub"] is None


def test_negative_cost_price_is_rejected(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")

    resp = client.put(f"/api/stores/{d['store_a'].id}/products/{product.id}/cost-price", json={"cost_price_rub": "-5"})
    assert resp.status_code == 422


def test_viewer_role_cannot_set_cost_price(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id)
    viewer = User(email="cost_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "cost_viewer@example.com", "password123")
    resp = client.put(f"/api/stores/{d['store_a'].id}/products/{product.id}/cost-price", json={"cost_price_rub": "10"})
    assert resp.status_code == 403


def test_cannot_set_cost_price_for_another_stores_product(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id)
    login(client, "owner_b@example.com", "password123")

    resp = client.put(f"/api/stores/{d['store_b'].id}/products/{product.id}/cost-price", json={"cost_price_rub": "10"})
    assert resp.status_code == 404
