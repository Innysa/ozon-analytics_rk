"""Tests for app.services.product_merge — merging two Product rows that
represent the same real-world Ozon product (the historic sku=0
duplicate-row bug: see the module's own docstring for how it arises, and
tests/test_ozon_product_sync.py /
tests/test_search_query_details_sync_service.py for the end-to-end
regression tests against the two sync paths that hit it live)."""
from datetime import datetime, timedelta, timezone

from app.models.change_history import ChangeHistory, ChangeType
from app.models.product import Product
from app.models.recommendation import Recommendation, RecommendationKind
from app.models.review import Review, ReviewSource, ReviewStatus
from app.services.product_merge import merge_duplicate_products, pick_survivor


def _make_product(db_session, store_id: str, sku: str, *, name: str = "Товар", created_at=None):
    product = Product(store_id=store_id, ozon_sku=sku, name=name)
    db_session.add(product)
    db_session.flush()
    if created_at is not None:
        product.created_at = created_at
        db_session.flush()
    return product


def test_pick_survivor_prefers_valid_sku_over_placeholder(db_session, two_stores_with_users):
    d = two_stores_with_users
    placeholder = _make_product(db_session, d["store_a"].id, "0")
    real = _make_product(db_session, d["store_a"].id, "123")

    keep, remove = pick_survivor(db_session, placeholder, real)
    assert keep.id == real.id
    assert remove.id == placeholder.id

    # Order-independent.
    keep2, remove2 = pick_survivor(db_session, real, placeholder)
    assert keep2.id == real.id
    assert remove2.id == placeholder.id


def test_pick_survivor_prefers_more_related_history_when_both_valid(db_session, two_stores_with_users):
    d = two_stores_with_users
    richer = _make_product(db_session, d["store_a"].id, "111")
    poorer = _make_product(db_session, d["store_a"].id, "222")
    db_session.add(Review(store_id=d["store_a"].id, product_id=richer.id, ozon_review_id="r1",
                           source=ReviewSource.OZON_API, rating=5, status=ReviewStatus.NEW))
    db_session.commit()

    keep, remove = pick_survivor(db_session, poorer, richer)
    assert keep.id == richer.id
    assert remove.id == poorer.id


def test_pick_survivor_tie_breaks_on_older_created_at(db_session, two_stores_with_users):
    d = two_stores_with_users
    now = datetime.now(timezone.utc)
    older = _make_product(db_session, d["store_a"].id, "111", created_at=now - timedelta(days=5))
    newer = _make_product(db_session, d["store_a"].id, "222", created_at=now)

    keep, remove = pick_survivor(db_session, newer, older)
    assert keep.id == older.id
    assert remove.id == newer.id


def test_merge_is_a_noop_for_the_same_product(db_session, two_stores_with_users):
    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id, "111")

    merge_duplicate_products(db_session, keep=product, remove=product)

    assert db_session.query(Product).filter(Product.id == product.id).count() == 1


def test_merge_backfills_missing_offer_id_and_product_id_onto_survivor(db_session, two_stores_with_users):
    """The surviving row isn't always the one that happened to carry
    offer_id/ozon_product_id (e.g. it could win purely on having more
    history) — losing that metadata would silently break the offer_id retry
    fallback for it going forward, so it must be backfilled from the loser
    rather than discarded."""
    d = two_stores_with_users
    keep = _make_product(db_session, d["store_a"].id, "111", name="Выживший")
    remove = _make_product(db_session, d["store_a"].id, "0", name="Дубликат")
    remove.offer_id = "art-1"
    remove.ozon_product_id = 42
    db_session.commit()

    merge_duplicate_products(db_session, keep=keep, remove=remove)

    assert keep.offer_id == "art-1"
    assert keep.ozon_product_id == 42


def test_merge_never_overwrites_keeps_own_offer_id_or_product_id(db_session, two_stores_with_users):
    d = two_stores_with_users
    keep = _make_product(db_session, d["store_a"].id, "111", name="Выживший")
    keep.offer_id = "art-keep"
    keep.ozon_product_id = 1
    remove = _make_product(db_session, d["store_a"].id, "0", name="Дубликат")
    remove.offer_id = "art-remove"
    remove.ozon_product_id = 2
    db_session.commit()

    merge_duplicate_products(db_session, keep=keep, remove=remove)

    assert keep.offer_id == "art-keep"
    assert keep.ozon_product_id == 1


def test_merge_reassigns_every_referencing_table_and_deletes_the_loser(db_session, two_stores_with_users):
    d = two_stores_with_users
    keep = _make_product(db_session, d["store_a"].id, "111", name="Выживший")
    remove = _make_product(db_session, d["store_a"].id, "0", name="Дубликат")

    review = Review(store_id=d["store_a"].id, product_id=remove.id, ozon_review_id="r1",
                     source=ReviewSource.OZON_API, rating=4, status=ReviewStatus.NEW)
    recommendation = Recommendation(store_id=d["store_a"].id, product_id=remove.id,
                                     kind=RecommendationKind.GENERAL, text="Улучшить фото")
    history = ChangeHistory(store_id=d["store_a"].id, product_id=remove.id,
                             change_type=ChangeType.PRICE, changed_at=datetime.now(timezone.utc),
                             description="Снизили цену")
    db_session.add_all([review, recommendation, history])
    db_session.commit()

    merge_duplicate_products(db_session, keep=keep, remove=remove)
    db_session.commit()

    db_session.expire_all()
    assert db_session.query(Product).filter(Product.id == remove.id).count() == 0
    assert db_session.get(Review, review.id).product_id == keep.id
    assert db_session.get(Recommendation, recommendation.id).product_id == keep.id
    assert db_session.get(ChangeHistory, history.id).product_id == keep.id
