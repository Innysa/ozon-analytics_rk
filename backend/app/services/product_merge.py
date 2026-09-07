"""Merging duplicate Product rows that represent the same real-world Ozon
product.

How the duplicate arises: Ozon's product-info API can report sku=0 ("no SKU
assigned yet") for a product that already has a real SKU assigned elsewhere
(see the sku=0 fixes in app.api.routes.sync's sync_ozon_products and
app.services.search_query_details_sync_service). If a "placeholder" row
(ozon_sku="0"/empty) was ever saved for that product *before* a separate row
already existed under the real sku — e.g. one sync path created the real-sku
row while an older, unfixed sync run had already created the sku=0 row for
the same offer_id/product_id — both rows sit side by side in the same store,
sharing the same offer_id/ozon_product_id, until something tries to correct
the placeholder's sku to the now-known real value. That write collides with
the OTHER row's sku under this store's (store_id, ozon_sku) unique
constraint (uq_product_store_sku), raising a psycopg IntegrityError instead
of silently resolving — confirmed live for offer_id "мус/вед/бел1/3"
(sku 5716615794).

merge_duplicate_products() is the one place both sync paths call *before*
ever writing a resolved sku onto an existing Product row when a collision is
detected: it reassigns every FK-referencing table's rows from the losing
duplicate onto the surviving product, deletes the loser, and lets the caller
finish updating the survivor normally. pick_survivor() decides which of two
duplicate rows should be kept, so history (reviews, statistics, recommendations)
is preserved on the row that actually has it, not discarded arbitrarily.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
from app.models.advertising_statistic import AdvertisingStatistic
from app.models.change_history import ChangeHistory
from app.models.product import Product
from app.models.product_card_statistic import ProductCardStatistic
from app.models.recommendation import Recommendation
from app.models.review import Review
from app.models.search_query_statistic import SearchQueryStatistic

# Every model with a product_id FK onto Product. Kept as one list so a new
# product-referencing table is a one-line addition here rather than a
# scattered bug the next time two Product rows need merging.
_REASSIGN_MODELS = [
    Review,
    AdvertisingStatistic,
    AdvertisingDailyStatistic,
    ProductCardStatistic,
    Recommendation,
    ChangeHistory,
    SearchQueryStatistic,
]


def _is_positive_sku(value: str | None) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _related_row_count(db: Session, product_id: str) -> int:
    return sum(
        db.query(model).filter(model.product_id == product_id).count() for model in _REASSIGN_MODELS
    )


def pick_survivor(db: Session, a: Product, b: Product) -> tuple[Product, Product]:
    """Returns (keep, remove) between two duplicate Product rows believed to
    represent the same real-world product. A placeholder sku=0/empty row
    never survives over one that already carries a valid sku; if both or
    neither do, whichever has more related history (reviews, statistics,
    recommendations, change history) wins, since that's the row sellers
    actually rely on; ties fall back to the older row as the presumed
    original."""
    a_valid, b_valid = _is_positive_sku(a.ozon_sku), _is_positive_sku(b.ozon_sku)
    if a_valid != b_valid:
        return (a, b) if a_valid else (b, a)
    a_count, b_count = _related_row_count(db, a.id), _related_row_count(db, b.id)
    if a_count != b_count:
        return (a, b) if a_count > b_count else (b, a)
    return (a, b) if a.created_at <= b.created_at else (b, a)


def merge_duplicate_products(db: Session, *, keep: Product, remove: Product) -> None:
    """Reassigns every row referencing `remove` onto `keep`, then deletes
    `remove`. Both must belong to the same store — callers decide which one
    survives (see pick_survivor). A no-op if keep and remove are the same
    row. Flushes so the deletion (and the freed-up ozon_sku) is immediately
    visible to the caller's own subsequent writes.

    Backfills `keep.offer_id`/`ozon_product_id` from `remove` if `keep` is
    missing them (never overwrites a value `keep` already has) — the
    surviving row isn't always the one that happened to carry these, e.g.
    when the winner was picked for its sku validity or its history rather
    than its metadata completeness, and losing them would silently break
    the offer_id-based retry fallback for that row going forward."""
    if keep.id == remove.id:
        return
    if not keep.offer_id and remove.offer_id:
        keep.offer_id = remove.offer_id
    if keep.ozon_product_id is None and remove.ozon_product_id is not None:
        keep.ozon_product_id = remove.ozon_product_id
    for model in _REASSIGN_MODELS:
        db.query(model).filter(model.product_id == remove.id).update(
            {"product_id": keep.id}, synchronize_session=False
        )
    db.delete(remove)
    db.flush()
