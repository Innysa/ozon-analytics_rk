"""Pulls the seller's product catalog (offer_id/sku/price/stocks) from Ozon
Seller API: /v3/product/list for the id list, then /v3/product/info/list in
batches for the actual details, since list responses only carry ids.

Shared by two callers that must stay in sync: the manual "Синхронизировать с
Ozon" button (app.api.routes.sync) and the nightly scheduled job
(app.services.scheduler) — one implementation so both paths behave
identically and a bug fix here fixes both.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.encryption import decrypt_secret
from app.models.ozon_credentials import OzonCredentials
from app.models.product import Product
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.services.audit import record_audit
from app.services.ozon.client import OzonCredentials as OzonClientCredentials
from app.services.ozon.client import OzonSellerClient
from app.services.ozon.exceptions import OzonAPIError, OzonAuthError, OzonFeatureUnavailable

PRODUCT_INFO_BATCH_SIZE = 100  # Ozon's /v3/product/info/list caps ids per request


def _to_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def sync_store_products(
    db: Session, store_id: str, creds: OzonCredentials, *, initiated_by_user_id: str | None
) -> SyncRun:
    """Runs one product sync for a single store and returns the finished
    SyncRun. `initiated_by_user_id` is None for the automated nightly job —
    SyncRun.initiated_by_user_id is nullable for exactly this case."""
    run = SyncRun(
        store_id=store_id,
        initiated_by_user_id=initiated_by_user_id,
        source_type=SyncSourceType.OZON_PRODUCTS_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(
        db, action="sync_started", user_id=initiated_by_user_id, store_id=store_id, target_type="sync_run", target_id=run.id
    )
    db.commit()

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)

    existing_by_sku = {p.ozon_sku: p for p in db.query(Product).filter(Product.store_id == store_id).all()}

    fetched = created = updated = 0
    error_message = None
    try:
        with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
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

            for batch_start in range(0, len(product_ids), PRODUCT_INFO_BATCH_SIZE):
                batch = product_ids[batch_start : batch_start + PRODUCT_INFO_BATCH_SIZE]
                info = client.get_products_info(batch)
                for item in info.items:
                    fetched += 1
                    if item.sku is None:
                        continue
                    sku = str(item.sku)

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

                    product = existing_by_sku.get(sku)
                    if product:
                        product.ozon_product_id = item.id
                        product.offer_id = item.offer_id
                        product.name = item.name or product.name
                        product.image_url = image_url or product.image_url
                        product.price_rub = price
                        product.old_price_rub = old_price
                        product.fbo_stock = fbo_stock
                        product.fbs_stock = fbs_stock
                        product.is_archived = bool(item.is_archived)
                        updated += 1
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
                            fbo_stock=fbo_stock,
                            fbs_stock=fbs_stock,
                            is_archived=bool(item.is_archived),
                        )
                        db.add(product)
                        existing_by_sku[sku] = product
                        created += 1
        run.status = SyncStatus.SUCCESS
    except OzonAuthError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except OzonFeatureUnavailable as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except OzonAPIError as exc:
        run.status = SyncStatus.PARTIAL if created or updated else SyncStatus.FAILED
        error_message = str(exc)

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = fetched
    run.items_created = created
    run.items_skipped_duplicate = updated  # "duplicates" here means products already known and refreshed
    run.error_message = error_message
    db.flush()
    record_audit(
        db,
        action="sync_finished",
        user_id=initiated_by_user_id,
        store_id=store_id,
        target_type="sync_run",
        target_id=run.id,
        result="success" if run.status == SyncStatus.SUCCESS else "failure",
        message=error_message,
    )
    db.commit()
    return run
