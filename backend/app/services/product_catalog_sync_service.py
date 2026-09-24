"""Pulls the seller's product catalog (offer_id/sku/price/stocks) from Ozon
Seller API: /v3/product/list for the id list, then /v3/product/info/list in
batches for the actual details, since list responses only carry ids.

EXTRACTED 2026-09-22 from app.api.routes.sync's `sync_ozon_products` route
handler (mechanical extraction — no behavior change, same logic, same
tests) so app.services.product_catalog_scheduler can call the exact same
code automatically once a day, the same "service function the route AND a
scheduler both call" shape every other sync in this app already has. The
route itself now just resolves credentials/SyncRun bookkeeping around a
call to sync_product_catalog() below.

A product queried by product_id can come back with sku=0 even though a
real SKU is already assigned (confirmed live: a "нет на складе" product,
not yet delivered to an Ozon warehouse, still had a real SKU visible in
Ozon's own "Аналитика" section) — such items get one extra batched lookup
of the same endpoint by offer_id instead, which resolves the real sku.
Only ever applied to the zero-sku subset, not every product.

Also captures ProductPriceDailySnapshot — see that model's own docstring —
in the SAME upsert loop.

**ДОБАВЛЕНО 2026-09-24**: also calls POST /v5/product/info/prices per batch
(one extra Ozon call per PRODUCT_INFO_BATCH_SIZE products — see
OzonSellerClient.get_product_prices()'s own docstring for the confirmed
contract) to capture marketing_seller_price_rub, the correct «Ваша цена»
base for «СПП (расчёт)» — price_rub (from /v3/product/info/list alone)
turned out to be the no-promo ceiling price, not the seller's active
listing price. See order_daily_sync_service.py's own docstring for the
full real-account trail that found this.

hard_failure on the returned outcome distinguishes an auth/feature-tier
failure (OzonAuthError/OzonFeatureUnavailable, or an unhandled internal
exception) from an ordinary OzonAPIError: callers treat the former as
always FAILED regardless of partial progress, the latter as PARTIAL if
anything was actually created/updated before it hit — same distinction
the route's own exception handling made before this extraction."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.product_price_daily_snapshot import ProductPriceDailySnapshot
from app.services.ozon.exceptions import OzonAPIError, OzonAuthError, OzonFeatureUnavailable
from app.services.product_merge import merge_duplicate_products, pick_survivor

logger = logging.getLogger(__name__)

PRODUCT_INFO_BATCH_SIZE = 100  # Ozon's /v3/product/info/list caps ids per request


def _to_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


@dataclass
class ProductCatalogSyncOutcome:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)
    hard_failure: bool = False  # auth/feature-tier/internal — always FAILED, never PARTIAL, regardless of progress


def sync_product_catalog(db: Session, *, store_id: str, client) -> ProductCatalogSyncOutcome:
    outcome = ProductCatalogSyncOutcome()

    all_existing_products = db.query(Product).filter(Product.store_id == store_id).all()
    existing_by_sku = {p.ozon_sku: p for p in all_existing_products}
    # Matched separately from existing_by_sku, and consulted FIRST: Ozon's
    # own product_id is the stable identity for a product, unlike sku, which
    # can start at the sentinel 0 ("no SKU assigned yet") and later become a
    # real positive value once Ozon activates the product into a scheme.
    # Confirmed the hard way: before this, a product stuck at sku=0 (created
    # by an older version of this sync, back when it didn't skip sku=0 at
    # all) stayed an orphaned row forever — re-syncing after Ozon assigned
    # it a real sku matched nothing in existing_by_sku (the dict was keyed
    # by the OLD "0"), so a brand-new duplicate Product row was created
    # instead of the old one being corrected in place.
    existing_by_product_id = {
        p.ozon_product_id: p for p in all_existing_products if p.ozon_product_id is not None
    }

    # See ProductPriceDailySnapshot's own docstring — a daily history of
    # price_rub/old_price_rub/fbo_stock/fbs_stock captured on every catalog
    # sync, so «СПП (расчёт)» can use a SKU's price on its OWN historical
    # day instead of always today's snapshot. Preloaded once (not queried
    # per product inside _upsert) — a sync can touch thousands of products.
    price_snapshot_date = datetime.now(timezone.utc).date()
    existing_snapshots_today = {
        s.ozon_sku: s
        for s in db.query(ProductPriceDailySnapshot).filter(
            ProductPriceDailySnapshot.store_id == store_id,
            ProductPriceDailySnapshot.date == price_snapshot_date,
        ).all()
    }

    try:
        product_ids: list[int] = []
        last_id = ""
        for _ in range(50):  # hard cap on pages per run to avoid runaway loops
            page = client.list_products(last_id=last_id)
            items = page.result.items
            if not items:
                break
            product_ids.extend(item.product_id for item in items)
            if not page.result.last_id or page.result.last_id == last_id:
                break
            last_id = page.result.last_id

        def _fetch_marketing_seller_prices(offer_ids: list[str]) -> dict[str, Decimal | None]:
            """One batch, one call — offer_ids is already <= PRODUCT_INFO_
            BATCH_SIZE (100) here, so no further chunking is needed. Returns
            {} on an empty input rather than making a pointless call.

            Deliberately NEVER lets an OzonAPIError (or a parsing surprise
            in Ozon's response) propagate out of here and abort the WHOLE
            catalog sync — marketing_seller_price_rub is an ENHANCEMENT to
            an already-working sync (price/old_price/stock/name from
            /v3/product/info/list), not something the rest of the sync
            should depend on succeeding. A failure here is logged and
            surfaced as a non-fatal outcome.errors note instead."""
            if not offer_ids:
                return {}
            try:
                prices = client.get_product_prices(offer_ids)
            except OzonAPIError as exc:
                logger.warning("Товары: не удалось получить marketing_seller_price для %d SKU: %s", len(offer_ids), exc)
                outcome.errors.append(f"«Ваша цена» (СПП) не обновлена для части товаров: {exc}")
                return {}
            result = {
                item.offer_id: _to_decimal(str(item.price.marketing_seller_price))
                for item in prices.items
                if item.offer_id and item.price is not None and item.price.marketing_seller_price is not None
            }
            if offer_ids and not result:
                logger.warning(
                    "Товары: /v5/product/info/prices вернул 0 цен для %d запрошенных offer_id — "
                    "проверьте форму ответа, если это повторяется",
                    len(offer_ids),
                )
            return result

        def _upsert(item, sku: str, marketing_seller_price: Decimal | None) -> None:
            fbo_stock = fbs_stock = 0
            if item.stocks:
                for stock in item.stocks.stocks:
                    if stock.source == "fbo":
                        fbo_stock += stock.present or 0
                    elif stock.source == "fbs":
                        fbs_stock += stock.present or 0

            image_url = item.primary_image[0] if item.primary_image else None
            price = _to_decimal(item.price)
            old_price = _to_decimal(item.old_price)

            by_id = existing_by_product_id.get(item.id)
            by_sku = existing_by_sku.get(sku)
            product = by_id or by_sku
            if by_id and by_sku and by_id.id != by_sku.id:
                # Same real-world product tracked under two rows — the
                # historic sku=0 duplicate-row bug (see
                # app.services.product_merge's module docstring: a
                # placeholder sku=0 row and a real-sku row for the same
                # offer_id/product_id, saved at different times). Merging
                # here, before the plain field assignment below, is what
                # avoids violating uq_product_store_sku (store_id,
                # ozon_sku) when `sku` gets written onto the survivor —
                # confirmed live for offer_id "мус/вед/бел1/3".
                keep, remove = pick_survivor(db, by_id, by_sku)
                removed_sku, removed_product_id = remove.ozon_sku, remove.ozon_product_id
                merge_duplicate_products(db, keep=keep, remove=remove)
                existing_by_sku.pop(removed_sku, None)
                existing_by_product_id.pop(removed_product_id, None)
                product = keep
            if product:
                product.ozon_sku = sku  # may correct a stale sku (e.g. 0 -> a newly assigned real sku)
                product.ozon_product_id = item.id
                product.offer_id = item.offer_id
                product.name = item.name or product.name
                product.image_url = image_url or product.image_url
                product.price_rub = price
                product.old_price_rub = old_price
                product.marketing_seller_price_rub = marketing_seller_price
                product.fbo_stock = fbo_stock
                product.fbs_stock = fbs_stock
                product.is_archived = bool(item.is_archived)
                outcome.updated += 1
            else:
                product = Product(
                    store_id=store_id,
                    ozon_sku=sku,
                    ozon_product_id=item.id,
                    offer_id=item.offer_id,
                    name=item.name or f"Товар SKU {sku}",
                    image_url=image_url,
                    price_rub=price,
                    old_price_rub=old_price,
                    marketing_seller_price_rub=marketing_seller_price,
                    fbo_stock=fbo_stock,
                    fbs_stock=fbs_stock,
                    is_archived=bool(item.is_archived),
                )
                db.add(product)
                outcome.created += 1
            existing_by_product_id[item.id] = product
            existing_by_sku[sku] = product

            snapshot = existing_snapshots_today.get(sku)
            if snapshot is None:
                snapshot = ProductPriceDailySnapshot(store_id=store_id, ozon_sku=sku, date=price_snapshot_date)
                db.add(snapshot)
                existing_snapshots_today[sku] = snapshot
            snapshot.price_rub = price
            snapshot.old_price_rub = old_price
            snapshot.marketing_seller_price_rub = marketing_seller_price
            snapshot.fbo_stock = fbo_stock
            snapshot.fbs_stock = fbs_stock
            snapshot.source = "ozon_seller_api"

        # Confirmed on a real account: /v3/product/info/list queried by
        # product_id can hand back sku=0 ("no SKU assigned yet") for a
        # product that is "нет на складе" (not yet delivered to an Ozon
        # warehouse), even though a real SKU is already assigned and
        # visible in Ozon's own "Аналитика" section — e.g. offer_id
        # "мус/вед/бел1/3" (sku 5716615794) reproduced this exactly.
        # Re-querying the SAME endpoint by offer_id instead of
        # product_id resolves the real sku for such products. Only the
        # zero-sku subset is retried this way (not every product), so a
        # normal sync isn't slowed down by it.
        zero_sku_items = []
        for batch_start in range(0, len(product_ids), PRODUCT_INFO_BATCH_SIZE):
            batch = product_ids[batch_start : batch_start + PRODUCT_INFO_BATCH_SIZE]
            info = client.get_products_info(batch)
            prices_by_offer_id = _fetch_marketing_seller_prices([item.offer_id for item in info.items if item.offer_id])
            for item in info.items:
                outcome.fetched += 1
                if item.sku:
                    _upsert(item, str(item.sku), prices_by_offer_id.get(item.offer_id))
                elif item.offer_id:
                    zero_sku_items.append(item)
                # else: sku=0 AND no offer_id to retry with — nothing more to try.

        if zero_sku_items:
            logger.info(
                "Товары: %d товар(ов) вернулись с sku=0 по product_id — уточняю по offer_id",
                len(zero_sku_items),
            )
            offer_ids = [item.offer_id for item in zero_sku_items]
            resolved_by_offer_id = {}
            for batch_start in range(0, len(offer_ids), PRODUCT_INFO_BATCH_SIZE):
                batch = offer_ids[batch_start : batch_start + PRODUCT_INFO_BATCH_SIZE]
                retry_info = client.get_products_info_by_offer_id(batch)
                for retry_item in retry_info.items:
                    if retry_item.offer_id:
                        resolved_by_offer_id[retry_item.offer_id] = retry_item

            zero_sku_prices_by_offer_id = _fetch_marketing_seller_prices(
                [item.offer_id for item in zero_sku_items if item.offer_id]
            )
            for item in zero_sku_items:
                resolved = resolved_by_offer_id.get(item.offer_id)
                if resolved and resolved.sku:
                    _upsert(resolved, str(resolved.sku), zero_sku_prices_by_offer_id.get(item.offer_id))
                # else: still sku=0/missing even by offer_id — genuinely
                # unresolved for now; left for search_query_details_sync
                # _service's own named diagnostic to surface if this row
                # is ever fed into that sync.
    except OzonAuthError as exc:
        outcome.hard_failure = True
        outcome.errors.append(str(exc))
    except OzonFeatureUnavailable as exc:
        outcome.hard_failure = True
        outcome.errors.append(str(exc))
    except OzonAPIError as exc:
        outcome.errors.append(str(exc))
    except Exception as exc:
        # Anything else — e.g. a duplicate-key IntegrityError not already
        # prevented by product_merge — leaves this run's pending changes in
        # an indeterminate state. Roll them back rather than risk a
        # half-applied transaction; without this, the failed write would
        # leave the session unusable (PendingRollbackError) for every
        # statement below, including the very code that's supposed to mark
        # this run FAILED — so the run would stay stuck "running" forever
        # instead. created/updated are still reported as a diagnostic of
        # how far the run got, even though rollback means none of it was
        # actually persisted.
        db.rollback()
        outcome.hard_failure = True
        outcome.errors.append(f"Внутренняя ошибка: {exc}")
        logger.exception("Товары: непредвиденная ошибка синхронизации каталога, store_id=%s", store_id)

    return outcome
