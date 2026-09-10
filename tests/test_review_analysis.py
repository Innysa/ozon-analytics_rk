"""Tests for review AI analysis: the per-review "Проанализировать" route,
the product-scoped bulk route (app.api.routes.reviews.bulk_analyze_product_
reviews — added because the "Рекомендации ИИ" product tab aggregates ONLY
from ReviewAIAnalysis rows, and the only way to create one used to be
clicking "Проанализировать" on every single review), and that "Идеи для
инфографики" is now its own AI field rather than a duplicate of "Рекомендации
по карточке товара" (app.services.analytics_service.compute_review_analytics)."""
from datetime import datetime, timezone

from tests.conftest import login


def _seed_product_with_reviews(db_session, store_id, *, count=2, product_name="Товар"):
    from app.models.product import Product
    from app.models.review import Review, ReviewSource, ReviewStatus

    product = Product(store_id=store_id, ozon_sku="SKU-ANALYZE", name=product_name)
    db_session.add(product)
    db_session.flush()
    reviews = []
    for i in range(count):
        review = Review(
            store_id=store_id, product_id=product.id, ozon_review_id=f"rev-{i}",
            source=ReviewSource.CSV_IMPORT, rating=2, text=f"Отзыв {i}: не очень",
            status=ReviewStatus.NEW, published_at=datetime.now(timezone.utc),
        )
        db_session.add(review)
        reviews.append(review)
    db_session.commit()
    return product, reviews


def test_analyze_review_creates_review_ai_analysis(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product, reviews = _seed_product_with_reviews(db_session, d["store_a"].id, count=1)
    login(client, "owner_a@example.com", "password123")

    resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/{reviews[0].id}/analyze")
    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis"] is not None
    assert body["analysis"]["sentiment"] in ("positive", "neutral", "negative")

    from app.models.review_ai_analysis import ReviewAIAnalysis
    row = db_session.query(ReviewAIAnalysis).filter(ReviewAIAnalysis.review_id == reviews[0].id).one()
    assert row.model_used == "demo"


def test_bulk_analyze_product_reviews_analyzes_every_unanalyzed_review(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product, reviews = _seed_product_with_reviews(db_session, d["store_a"].id, count=3)
    login(client, "owner_a@example.com", "password123")

    resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/bulk/analyze-product?product_id={product.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"succeeded": 3, "failed": 0, "skipped": 0}

    from app.models.review_ai_analysis import ReviewAIAnalysis
    rows = db_session.query(ReviewAIAnalysis).filter(ReviewAIAnalysis.review_id.in_([r.id for r in reviews])).all()
    assert len(rows) == 3


def test_bulk_analyze_product_reviews_skips_already_analyzed(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product, reviews = _seed_product_with_reviews(db_session, d["store_a"].id, count=2)
    login(client, "owner_a@example.com", "password123")

    # Analyze the first review individually first.
    client.post(f"/api/stores/{d['store_a'].id}/reviews/{reviews[0].id}/analyze")

    resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/bulk/analyze-product?product_id={product.id}")
    assert resp.status_code == 200
    assert resp.json() == {"succeeded": 1, "failed": 0, "skipped": 1}


def test_bulk_analyze_product_reviews_second_call_is_a_noop(client, db_session, two_stores_with_users):
    """Safe to click repeatedly — a second call with nothing new to analyze
    should skip everything rather than re-analyzing (which would also
    silently discard any manual correction, if that existed)."""
    d = two_stores_with_users
    product, reviews = _seed_product_with_reviews(db_session, d["store_a"].id, count=2)
    login(client, "owner_a@example.com", "password123")

    client.post(f"/api/stores/{d['store_a'].id}/reviews/bulk/analyze-product?product_id={product.id}")
    resp2 = client.post(f"/api/stores/{d['store_a'].id}/reviews/bulk/analyze-product?product_id={product.id}")
    assert resp2.json() == {"succeeded": 0, "failed": 0, "skipped": 2}


def test_bulk_analyze_product_reviews_store_isolation(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    product_b, _ = _seed_product_with_reviews(db_session, d["store_b"].id, count=1)
    login(client, "owner_a@example.com", "password123")

    resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/bulk/analyze-product?product_id={product_b.id}")
    assert resp.status_code == 404


def test_bulk_analyze_product_reviews_requires_manager_role(client, db_session, two_stores_with_users):
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User
    from app.core.security import hash_password

    d = two_stores_with_users
    product, _ = _seed_product_with_reviews(db_session, d["store_a"].id, count=1)
    viewer = User(email="viewer_a@example.com", password_hash=hash_password("password123"), full_name="Viewer")
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "viewer_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/reviews/bulk/analyze-product?product_id={product.id}")
    assert resp.status_code == 403


def test_review_analytics_infographic_ideas_is_distinct_from_card_improvements(client, db_session, two_stores_with_users):
    """Regression test for the bug this session fixed: "Идеи для
    инфографики" used to be computed from the SAME card_improvements_json
    as "Рекомендации по карточке товара" (no dedicated AI field existed),
    so the two sections always showed identical content. Confirms they can
    now differ."""
    import json

    from app.models.review import Review, ReviewSource, ReviewStatus
    from app.models.review_ai_analysis import ReviewAIAnalysis
    from app.models.product import Product

    d = two_stores_with_users
    store_id = d["store_a"].id
    product = Product(store_id=store_id, ozon_sku="SKU-INFO", name="Товар")
    db_session.add(product)
    db_session.flush()
    review = Review(
        store_id=store_id, product_id=product.id, ozon_review_id="rev-info",
        source=ReviewSource.CSV_IMPORT, rating=2, text="Непонятен размер",
        status=ReviewStatus.NEW, published_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(ReviewAIAnalysis(
        review_id=review.id, store_id=store_id, sentiment="negative", category="size", urgency="medium",
        reply_needed=True,
        card_improvements_json=json.dumps(["Добавить размерную сетку в описание"]),
        infographic_ideas_json=json.dumps(["Добавить фото с линейкой рядом с товаром"]),
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/analytics/reviews?product_id={product.id}")
    assert resp.status_code == 200
    body = resp.json()
    card_labels = [i["label"] for i in body["card_improvement_ideas"]]
    infographic_labels = [i["label"] for i in body["infographic_ideas"]]
    assert card_labels == ["Добавить размерную сетку в описание"]
    assert infographic_labels == ["Добавить фото с линейкой рядом с товаром"]
    assert card_labels != infographic_labels
