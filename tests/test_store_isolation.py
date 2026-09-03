"""Tests proving that a user of one store cannot reach another store's data
through a direct API request, even by supplying the other store's ID by hand.
This is the core safety property required by the spec."""
from tests.conftest import login


def test_owner_cannot_list_other_store(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.get("/api/stores")
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()}
    assert d["store_a"].id in ids
    assert d["store_b"].id not in ids


def test_owner_cannot_read_other_store_reviews(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.get(f"/api/stores/{d['store_b'].id}/reviews")
    assert resp.status_code == 403


def test_owner_cannot_read_other_store_products(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.get(f"/api/stores/{d['store_b'].id}/products")
    assert resp.status_code == 403


def test_owner_cannot_read_other_store_ai_settings(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.get(f"/api/stores/{d['store_b'].id}/ai-settings")
    assert resp.status_code == 403


def test_owner_cannot_read_other_store_ozon_credentials(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.get(f"/api/stores/{d['store_b'].id}/ozon/credentials")
    assert resp.status_code == 403


def test_owner_cannot_read_other_store_change_history(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.get(f"/api/stores/{d['store_b'].id}/change-history")
    assert resp.status_code == 403


def test_owner_can_access_own_store(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.get(f"/api/stores/{d['store_a'].id}/reviews")
    assert resp.status_code == 200


def test_admin_can_access_both_stores(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "admin@example.com", "adminpass123")

    resp_a = client.get(f"/api/stores/{d['store_a'].id}/reviews")
    resp_b = client.get(f"/api/stores/{d['store_b'].id}/reviews")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    resp_list = client.get("/api/stores")
    ids = {s["id"] for s in resp_list.json()}
    assert d["store_a"].id in ids and d["store_b"].id in ids


def test_unauthenticated_request_rejected(client, two_stores_with_users):
    d = two_stores_with_users
    resp = client.get(f"/api/stores/{d['store_a'].id}/reviews")
    assert resp.status_code == 401


def test_reviews_do_not_leak_across_stores_in_list(client, db_session, two_stores_with_users):
    from datetime import datetime, timezone

    from app.models.product import Product
    from app.models.review import Review, ReviewSource, ReviewStatus

    d = two_stores_with_users
    product_a = Product(store_id=d["store_a"].id, ozon_sku="SKU-A", name="Товар А")
    product_b = Product(store_id=d["store_b"].id, ozon_sku="SKU-B", name="Товар Б")
    db_session.add_all([product_a, product_b])
    db_session.flush()

    review_a = Review(
        store_id=d["store_a"].id, product_id=product_a.id, ozon_review_id="rev-a-1",
        source=ReviewSource.CSV_IMPORT, rating=5, text="Отлично",
        status=ReviewStatus.NEW, published_at=datetime.now(timezone.utc),
    )
    review_b = Review(
        store_id=d["store_b"].id, product_id=product_b.id, ozon_review_id="rev-b-1",
        source=ReviewSource.CSV_IMPORT, rating=1, text="Плохо",
        status=ReviewStatus.NEW, published_at=datetime.now(timezone.utc),
    )
    db_session.add_all([review_a, review_b])
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/reviews")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["ozon_review_id"] == "rev-a-1"

    # Same user, correct own store_id, cannot see store B's review even indirectly.
    review_ids = [item["ozon_review_id"] for item in items]
    assert "rev-b-1" not in review_ids
