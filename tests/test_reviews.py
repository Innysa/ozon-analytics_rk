"""Tests for the review reply workflow: draft -> edit -> approve -> publish,
with the mandatory human-approval gate enforced by the API (not just the UI)."""
from datetime import datetime, timezone

from tests.conftest import login


def _seed_review(db_session, store_id, product_name="Товар"):
    from app.models.product import Product
    from app.models.review import Review, ReviewSource, ReviewStatus

    product = Product(store_id=store_id, ozon_sku="SKU-1", name=product_name)
    db_session.add(product)
    db_session.flush()
    review = Review(
        store_id=store_id, product_id=product.id, ozon_review_id="rev-1",
        source=ReviewSource.CSV_IMPORT, rating=2, text="Не очень",
        status=ReviewStatus.NEW, published_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.commit()
    return review


def test_generate_draft_creates_draft_status(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    review = _seed_review(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")

    resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/{review.id}/generate-draft")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "draft_created"
    assert body["latest_draft"]["status"] == "draft"
    assert body["latest_draft"]["generated_by_ai"] is True


def test_cannot_publish_without_approval(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    review = _seed_review(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")

    draft_resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/{review.id}/generate-draft")
    comment_id = draft_resp.json()["latest_draft"]["id"]

    publish_resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/{review.id}/comments/{comment_id}/publish")
    assert publish_resp.status_code == 400


def test_approve_then_publish_flow_reaches_gate(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    review = _seed_review(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")

    draft_resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/{review.id}/generate-draft")
    comment_id = draft_resp.json()["latest_draft"]["id"]

    approve_resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/{review.id}/comments/{comment_id}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "approved"

    # No Ozon credentials configured for this store -> publish must fail gracefully,
    # not crash, and point to manual copy instead.
    publish_resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/{review.id}/comments/{comment_id}/publish")
    assert publish_resp.status_code == 409
    assert "скопируйте" in publish_resp.json()["detail"].lower() or "copy" in publish_resp.json()["detail"].lower()


def test_edit_only_allowed_before_approval(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    review = _seed_review(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")

    draft_resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/{review.id}/generate-draft")
    comment_id = draft_resp.json()["latest_draft"]["id"]

    edit_resp = client.patch(
        f"/api/stores/{d['store_a'].id}/reviews/{review.id}/comments/{comment_id}",
        json={"text": "Отредактированный ответ"},
    )
    assert edit_resp.status_code == 200
    assert edit_resp.json()["edited_by_user"] is True

    client.post(f"/api/stores/{d['store_a'].id}/reviews/{review.id}/comments/{comment_id}/approve")

    edit_after_approve = client.patch(
        f"/api/stores/{d['store_a'].id}/reviews/{review.id}/comments/{comment_id}",
        json={"text": "Ещё раз изменить"},
    )
    assert edit_after_approve.status_code == 400


def test_viewer_role_cannot_generate_draft(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    review = _seed_review(db_session, d["store_a"].id)

    viewer = User(email="viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/{review.id}/generate-draft")
    assert resp.status_code == 403
