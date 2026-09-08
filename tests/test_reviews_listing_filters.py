"""Tests for the two new GET /reviews listing filters: has_text (separates
star-only reviews from ones with a written comment) and sku (search by the
product's Ozon SKU or the seller's own offer_id/артикул)."""
from datetime import datetime, timezone

from tests.conftest import login


def _make_review(db_session, store_id, *, ozon_review_id, rating, text, ozon_sku=None, offer_id=None):
    from app.models.product import Product
    from app.models.review import Review, ReviewSource, ReviewStatus

    product_id = None
    if ozon_sku is not None:
        product = Product(store_id=store_id, ozon_sku=ozon_sku, offer_id=offer_id, name="Товар")
        db_session.add(product)
        db_session.flush()
        product_id = product.id

    review = Review(
        store_id=store_id, product_id=product_id, ozon_review_id=ozon_review_id,
        source=ReviewSource.CSV_IMPORT, rating=rating, text=text,
        status=ReviewStatus.NEW, published_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    return review


def test_has_text_true_returns_only_reviews_with_a_written_comment(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_review(db_session, d["store_a"].id, ozon_review_id="r1", rating=5, text=None)
    _make_review(db_session, d["store_a"].id, ozon_review_id="r2", rating=4, text="")
    _make_review(db_session, d["store_a"].id, ozon_review_id="r3", rating=5, text="Отличный товар")
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/reviews?has_text=true")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["ozon_review_id"] == "r3"


def test_has_text_false_returns_only_star_only_reviews(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_review(db_session, d["store_a"].id, ozon_review_id="r1", rating=5, text=None)
    _make_review(db_session, d["store_a"].id, ozon_review_id="r2", rating=4, text="")
    _make_review(db_session, d["store_a"].id, ozon_review_id="r3", rating=5, text="Отличный товар")
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/reviews?has_text=false")
    assert resp.status_code == 200
    ids = {i["ozon_review_id"] for i in resp.json()["items"]}
    assert ids == {"r1", "r2"}


def test_no_has_text_filter_returns_everything(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_review(db_session, d["store_a"].id, ozon_review_id="r1", rating=5, text=None)
    _make_review(db_session, d["store_a"].id, ozon_review_id="r2", rating=5, text="Хорошо")
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/reviews")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


def test_sku_filter_matches_ozon_sku_substring(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_review(db_session, d["store_a"].id, ozon_review_id="r1", rating=5, text="A", ozon_sku="4053047123", offer_id="пуф/S/сер1/2")
    _make_review(db_session, d["store_a"].id, ozon_review_id="r2", rating=4, text="B", ozon_sku="1492106823", offer_id="ковер/мех/сер/200")
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/reviews?sku=4053047")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["ozon_review_id"] == "r1"


def test_sku_filter_matches_offer_id_substring(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_review(db_session, d["store_a"].id, ozon_review_id="r1", rating=5, text="A", ozon_sku="4053047123", offer_id="пуф/S/сер1/2")
    _make_review(db_session, d["store_a"].id, ozon_review_id="r2", rating=4, text="B", ozon_sku="1492106823", offer_id="ковер/мех/сер/200")
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/reviews?sku=ковер")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["ozon_review_id"] == "r2"


def test_sku_filter_excludes_reviews_without_a_matched_product(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_review(db_session, d["store_a"].id, ozon_review_id="r1", rating=5, text="A")  # no product
    _make_review(db_session, d["store_a"].id, ozon_review_id="r2", rating=4, text="B", ozon_sku="123", offer_id="x")
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/reviews?sku=1")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["ozon_review_id"] == "r2"


def test_has_text_and_sku_filters_combine(client, db_session, two_stores_with_users):
    from app.models.product import Product
    from app.models.review import Review, ReviewSource, ReviewStatus

    d = two_stores_with_users
    product = Product(store_id=d["store_a"].id, ozon_sku="4053047123", offer_id="a", name="Товар")
    db_session.add(product)
    db_session.flush()
    db_session.add_all([
        Review(
            store_id=d["store_a"].id, product_id=product.id, ozon_review_id="r1",
            source=ReviewSource.CSV_IMPORT, rating=5, text=None,
            status=ReviewStatus.NEW, published_at=datetime.now(timezone.utc),
        ),
        Review(
            store_id=d["store_a"].id, product_id=product.id, ozon_review_id="r2",
            source=ReviewSource.CSV_IMPORT, rating=4, text="Хорошо",
            status=ReviewStatus.NEW, published_at=datetime.now(timezone.utc),
        ),
    ])
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/reviews?sku=4053047&has_text=true")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["ozon_review_id"] == "r2"
