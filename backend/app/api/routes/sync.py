import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.core.encryption import decrypt_secret
from app.db.session import SessionLocal, get_db
from app.models.advertising_campaign import AdvertisingCampaign
from app.models.membership import StoreRole
from app.models.ozon_credentials import OzonCredentials
from app.models.product import Product
from app.models.review import Review, ReviewSource, ReviewStatus
from app.models.store_rating_summary import StoreRatingSummary
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.models.user import User
from app.services.advertising_daily_sync_service import sync_advertising_daily_statistics
from app.services.audit import record_audit
from app.services.cash_flow_statement_sync_service import sync_cash_flow_statement_periods
from app.services.order_daily_sync_service import sync_order_daily_statistics
from app.services.ozon.client import OzonCredentials as OzonClientCredentials
from app.services.ozon.client import OzonSellerClient
from app.services.ozon.exceptions import OzonAPIError, OzonAuthError, OzonFeatureUnavailable
from app.services.product_analytics_daily_sync_service import sync_product_analytics_daily_statistics
from app.services.product_merge import merge_duplicate_products, pick_survivor
from app.services.search_query_details_sync_service import sync_search_query_details
from app.services.ozon_performance.client import OzonPerformanceClient
from app.services.ozon_performance.client import PerformanceCredentials as OzonPerfCredentials
from app.services.ozon_performance.exceptions import (
    OzonPerformanceAPIError,
    OzonPerformanceAuthError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stores/{store_id}/sync", tags=["sync"])

PRODUCT_INFO_BATCH_SIZE = 100  # Ozon's /v3/product/info/list caps ids per request


def _to_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _serialize(run: SyncRun) -> dict:
    return {
        "id": run.id,
        "source_type": run.source_type.value,
        "status": run.status.value,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "items_fetched": run.items_fetched,
        "items_created": run.items_created,
        "items_skipped_duplicate": run.items_skipped_duplicate,
        "error_message": run.error_message,
    }


@router.get("/runs")
def list_sync_runs(ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)), db: Session = Depends(get_db)) -> list[dict]:
    runs = db.scalars(
        select(SyncRun).where(SyncRun.store_id == ctx.store_id).order_by(SyncRun.started_at.desc()).limit(50)
    ).all()
    return [_serialize(r) for r in runs]


@router.post("/ozon-reviews")
def sync_ozon_reviews(
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для магазина не заданы ключи Ozon")

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.OZON_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(db, action="sync_started", user_id=user.id, store_id=ctx.store_id, target_type="sync_run", target_id=run.id)
    db.commit()

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)

    existing_ids = {
        r.ozon_review_id for r in db.query(Review.ozon_review_id).filter(Review.store_id == ctx.store_id).all()
    }

    fetched = created = skipped = 0
    error_message = None
    try:
        with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
            last_id = ""
            for _ in range(50):  # hard cap on pages per run to avoid runaway loops
                page = client.list_reviews(last_id=last_id)
                if not page.reviews:
                    break
                for item in page.reviews:
                    fetched += 1
                    review_id = str(item.id)
                    if review_id in existing_ids:
                        skipped += 1
                        continue

                    product_id = None
                    if item.sku is not None:
                        sku = str(item.sku)
                        product = db.query(Product).filter(Product.store_id == ctx.store_id, Product.ozon_sku == sku).first()
                        if not product:
                            product = Product(store_id=ctx.store_id, ozon_sku=sku, name=f"Товар SKU {sku}")
                            db.add(product)
                            db.flush()
                        product_id = product.id

                    review = Review(
                        store_id=ctx.store_id,
                        product_id=product_id,
                        ozon_review_id=review_id,
                        source=ReviewSource.OZON_API,
                        rating=item.rating or 0,
                        text=item.text,
                        status=ReviewStatus.NEW,
                        raw_payload=item.model_dump_json(),
                    )
                    db.add(review)
                    existing_ids.add(review_id)
                    created += 1

                if not page.has_next or not page.last_id:
                    break
                last_id = page.last_id
        run.status = SyncStatus.SUCCESS
    except OzonAuthError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except OzonFeatureUnavailable as exc:
        run.status = SyncStatus.FAILED
        error_message = (
            f"{exc} Загрузите отзывы вручную через CSV/XLSX на странице «Отзывы»."
        )
    except OzonAPIError as exc:
        run.status = SyncStatus.PARTIAL if created else SyncStatus.FAILED
        error_message = str(exc)

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = fetched
    run.items_created = created
    run.items_skipped_duplicate = skipped
    run.error_message = error_message
    db.flush()
    record_audit(
        db,
        action="sync_finished",
        user_id=user.id,
        store_id=ctx.store_id,
        target_type="sync_run",
        target_id=run.id,
        result="success" if run.status == SyncStatus.SUCCESS else "failure",
        message=error_message,
    )
    db.commit()
    return _serialize(run)


@router.post("/ozon-products")
def sync_ozon_products(
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Pulls the seller's product catalog (offer_id/sku/price/stocks) from
    Ozon Seller API: /v3/product/list for the id list, then /v3/product/info/list
    in batches for the actual details, since list responses only carry ids.

    A product queried by product_id can come back with sku=0 even though a
    real SKU is already assigned (confirmed live: a "нет на складе" product,
    not yet delivered to an Ozon warehouse, still had a real SKU visible in
    Ozon's own "Аналитика" section) — such items get one extra batched
    lookup of the same endpoint by offer_id instead, which resolves the real
    sku. Only ever applied to the zero-sku subset, not every product."""
    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для магазина не заданы ключи Ozon")

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.OZON_PRODUCTS_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(db, action="sync_started", user_id=user.id, store_id=ctx.store_id, target_type="sync_run", target_id=run.id)
    db.commit()

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)

    all_existing_products = db.query(Product).filter(Product.store_id == ctx.store_id).all()
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

    fetched = created = updated = 0
    skipped_invalid_sku: list[str] = []
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

            def _upsert(item, sku: str) -> None:
                nonlocal created, updated
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
                    product.fbo_stock = fbo_stock
                    product.fbs_stock = fbs_stock
                    product.is_archived = bool(item.is_archived)
                    updated += 1
                else:
                    product = Product(
                        store_id=ctx.store_id,
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
                    created += 1
                existing_by_product_id[item.id] = product
                existing_by_sku[sku] = product

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
                for item in info.items:
                    fetched += 1
                    if item.sku:
                        _upsert(item, str(item.sku))
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

                for item in zero_sku_items:
                    resolved = resolved_by_offer_id.get(item.offer_id)
                    if resolved and resolved.sku:
                        _upsert(resolved, str(resolved.sku))
                    # else: still sku=0/missing even by offer_id — genuinely
                    # unresolved for now; left for search_query_details_sync
                    # _service's own named diagnostic to surface if this row
                    # is ever fed into that sync.
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
        run.status = SyncStatus.FAILED
        error_message = f"Внутренняя ошибка: {exc}"
        logger.exception("Товары: непредвиденная ошибка синхронизации каталога, store_id=%s", ctx.store_id)

    if skipped_invalid_sku:
        skip_note = (
            f"Пропущено товаров с некорректным SKU (0/пусто) после повторного запроса по offer_id: "
            f"{len(skipped_invalid_sku)} ({', '.join(skipped_invalid_sku[:10])}"
            f"{'…' if len(skipped_invalid_sku) > 10 else ''})"
        )
        error_message = f"{error_message}; {skip_note}" if error_message else skip_note
        if run.status == SyncStatus.SUCCESS:
            run.status = SyncStatus.PARTIAL if (created or updated) else SyncStatus.FAILED

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = fetched
    run.items_created = created
    run.items_skipped_duplicate = updated  # "duplicates" here means products already known and refreshed
    run.error_message = error_message
    try:
        db.flush()
        record_audit(
            db,
            action="sync_finished",
            user_id=user.id,
            store_id=ctx.store_id,
            target_type="sync_run",
            target_id=run.id,
            result="success" if run.status == SyncStatus.SUCCESS else "failure",
            message=error_message,
        )
        db.commit()
    except Exception as exc:
        # A failure right here (e.g. a still-unforeseen duplicate-key
        # violation) must not leave `run` stuck at "running": roll back the
        # broken transaction first — required before the session can be used
        # again at all — then persist a FAILED status in a fresh one.
        db.rollback()
        logger.exception("Товары: не удалось сохранить результат синхронизации, store_id=%s", ctx.store_id)
        run.status = SyncStatus.FAILED
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = (
            f"{error_message}; Не удалось сохранить результат синхронизации: {exc}"
            if error_message
            else f"Не удалось сохранить результат синхронизации: {exc}"
        )
        db.flush()
        db.commit()
    return _serialize(run)


@router.post("/ozon-advertising")
def sync_ozon_advertising_campaigns(
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Syncs advertising campaign *metadata* (id, name, type, state, budget,
    dates) from Ozon Performance API. Deliberately does not touch day-by-day
    spend/clicks/orders — that requires the Performance API's async
    statistics-report flow, not implemented yet (see app.models.future)."""
    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds or not creds.performance_client_id_encrypted or not creds.performance_client_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для магазина не заданы ключи Ozon Performance API")

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.OZON_ADVERTISING_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(db, action="sync_started", user_id=user.id, store_id=ctx.store_id, target_type="sync_run", target_id=run.id)
    db.commit()

    perf_client_id = decrypt_secret(creds.performance_client_id_encrypted)
    perf_client_secret = decrypt_secret(creds.performance_client_secret_encrypted)

    existing_campaigns = {
        c.ozon_campaign_id: c
        for c in db.query(AdvertisingCampaign).filter(AdvertisingCampaign.store_id == ctx.store_id).all()
    }

    fetched = created = updated = 0
    error_message = None
    try:
        with OzonPerformanceClient(OzonPerfCredentials(client_id=perf_client_id, client_secret=perf_client_secret)) as client:
            campaigns = client.list_campaigns()
            for item in campaigns:
                fetched += 1
                campaign_id = str(item.id)
                existing = existing_campaigns.get(campaign_id)
                if existing:
                    existing.name = item.title
                    existing.campaign_type = item.advObjectType
                    existing.state = item.state
                    existing.daily_budget_rub = item.daily_budget_rub
                    existing.raw_payload = item.model_dump_json()
                    updated += 1
                else:
                    campaign = AdvertisingCampaign(
                        store_id=ctx.store_id,
                        ozon_campaign_id=campaign_id,
                        name=item.title,
                        campaign_type=item.advObjectType,
                        state=item.state,
                        daily_budget_rub=item.daily_budget_rub,
                        raw_payload=item.model_dump_json(),
                    )
                    db.add(campaign)
                    created += 1
        run.status = SyncStatus.SUCCESS
    except OzonPerformanceAuthError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except OzonPerformanceAPIError as exc:
        run.status = SyncStatus.PARTIAL if created or updated else SyncStatus.FAILED
        error_message = str(exc)

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = fetched
    run.items_created = created
    run.items_skipped_duplicate = updated  # "duplicates" here means campaigns already known and refreshed
    run.error_message = error_message
    db.flush()
    record_audit(
        db,
        action="sync_finished",
        user_id=user.id,
        store_id=ctx.store_id,
        target_type="sync_run",
        target_id=run.id,
        result="success" if run.status == SyncStatus.SUCCESS else "failure",
        message=error_message,
    )
    db.commit()
    return _serialize(run)


def _run_advertising_daily_statistics_sync(
    run_id: str,
    store_id: str,
    perf_client_id: str,
    perf_client_secret: str,
    date_from: date | None,
    date_to: date | None,
) -> None:
    """Runs in a FastAPI BackgroundTask, i.e. after the triggering request's
    own DB session has already been closed — uses its own SessionLocal(),
    never the request-scoped session. Can take several minutes (Ozon's async
    report flow, processed batch-by-batch — see
    app.services.advertising_daily_sync_service)."""
    db = SessionLocal()
    error_message = None
    try:
        run = db.get(SyncRun, run_id)
        try:
            with OzonPerformanceClient(OzonPerfCredentials(client_id=perf_client_id, client_secret=perf_client_secret)) as client:
                outcome = sync_advertising_daily_statistics(
                    db, store_id=store_id, client=client, date_from=date_from, date_to=date_to
                )
            run.items_fetched = outcome.fetched
            run.items_created = outcome.created
            run.items_skipped_duplicate = outcome.updated
            error_message = "; ".join(outcome.errors[:20]) if outcome.errors else None
            run.status = SyncStatus.SUCCESS if not outcome.errors else (
                SyncStatus.PARTIAL if (outcome.created or outcome.updated) else SyncStatus.FAILED
            )
        except OzonPerformanceAuthError as exc:
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except OzonPerformanceAPIError as exc:
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except Exception as exc:  # a SyncRun must never be left stuck "running" forever
            # sync_advertising_daily_statistics() commits per batch inside
            # itself — if that commit fails, the session is left in a
            # "pending rollback" state, and every statement below (including
            # the one meant to record this failure) would itself raise
            # PendingRollbackError instead of a clean FAILED status.
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = f"Внутренняя ошибка: {exc}"
            logger.exception("Реклама: непредвиденная ошибка автосинхронизации статистики, store_id=%s", store_id)

        run.finished_at = datetime.now(timezone.utc)
        run.error_message = error_message
        try:
            record_audit(
                db,
                action="sync_finished",
                store_id=store_id,
                target_type="sync_run",
                target_id=run.id,
                result="success" if run.status == SyncStatus.SUCCESS else "failure",
                message=error_message,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Реклама: не удалось сохранить результат синхронизации, store_id=%s", store_id)
            run.status = SyncStatus.FAILED
            run.error_message = error_message
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@router.post("/ozon-advertising-statistics")
def sync_ozon_advertising_daily_statistics(
    background_tasks: BackgroundTasks,
    date_from: date | None = None,
    date_to: date | None = None,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Triggers an automatic pull of daily advertising statistics (clicks,
    impressions, CTR, spend, orders, revenue per campaign/SKU/day) from Ozon
    Performance API's asynchronous statistics-report flow — the same data a
    seller could otherwise only get via the CSV upload on /advertising/statistics.
    Runs in the background (can take many minutes for many active campaigns,
    since Ozon allows only 1 report in flight per account — see
    app.services.advertising_daily_sync_service) and returns immediately with
    a SyncRun the frontend polls via GET /runs. date_from/date_to default to
    the last N days per ADVERTISING_STATS_DEFAULT_LOOKBACK_DAYS."""
    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds or not creds.performance_client_id_encrypted or not creds.performance_client_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для магазина не заданы ключи Ozon Performance API")

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.OZON_ADVERTISING_STATISTICS_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(db, action="sync_started", user_id=user.id, store_id=ctx.store_id, target_type="sync_run", target_id=run.id)
    db.commit()

    perf_client_id = decrypt_secret(creds.performance_client_id_encrypted)
    perf_client_secret = decrypt_secret(creds.performance_client_secret_encrypted)

    background_tasks.add_task(
        _run_advertising_daily_statistics_sync, run.id, ctx.store_id, perf_client_id, perf_client_secret, date_from, date_to
    )
    return _serialize(run)


def _run_search_query_details_sync(
    run_id: str,
    store_id: str,
    client_id: str,
    api_key: str,
    date_from: date | None,
    date_to: date | None,
) -> None:
    """Runs in a FastAPI BackgroundTask, i.e. after the triggering request's
    own DB session has already been closed — uses its own SessionLocal(),
    never the request-scoped session. See
    app.services.search_query_details_sync_service."""
    db = SessionLocal()
    error_message = None
    try:
        run = db.get(SyncRun, run_id)
        try:
            with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
                outcome = sync_search_query_details(db, store_id=store_id, client=client, date_from=date_from, date_to=date_to)
            run.items_fetched = outcome.fetched
            run.items_created = outcome.created
            run.items_skipped_duplicate = outcome.updated
            error_message = "; ".join(outcome.errors[:20]) if outcome.errors else None
            run.status = SyncStatus.SUCCESS if not outcome.errors else (
                SyncStatus.PARTIAL if (outcome.created or outcome.updated) else SyncStatus.FAILED
            )
        except OzonAuthError as exc:
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except OzonAPIError as exc:
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except Exception as exc:  # a SyncRun must never be left stuck "running" forever
            # sync_search_query_details() commits after every batch inside
            # itself — if that commit fails (e.g. a duplicate-key
            # IntegrityError), the session is left in a "pending rollback"
            # state, and every statement below — including the one meant to
            # record this very failure — would itself raise
            # PendingRollbackError instead of a clean FAILED status.
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = f"Внутренняя ошибка: {exc}"
            logger.exception("Позиции в поиске: непредвиденная ошибка автосинхронизации статистики, store_id=%s", store_id)

        run.finished_at = datetime.now(timezone.utc)
        run.error_message = error_message
        try:
            record_audit(
                db,
                action="sync_finished",
                store_id=store_id,
                target_type="sync_run",
                target_id=run.id,
                result="success" if run.status == SyncStatus.SUCCESS else "failure",
                message=error_message,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Позиции в поиске: не удалось сохранить результат синхронизации, store_id=%s", store_id)
            run.status = SyncStatus.FAILED
            run.error_message = error_message
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@router.post("/ozon-search-query-statistics")
def sync_ozon_search_query_statistics(
    background_tasks: BackgroundTasks,
    date_from: date | None = None,
    date_to: date | None = None,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Triggers an automatic pull of search-query statistics (search/view
    counts, position, conversion, orders per SKU/query) from Ozon Seller
    API's POST /v1/analytics/product-queries/details — the same data a
    seller could otherwise only get via the manual "Аналитика → Запросы"
    XLSX upload on /search-queries/upload. Runs in the background and
    returns immediately with a SyncRun the frontend polls via GET /runs.
    date_from/date_to default to the last N days per
    SEARCH_QUERY_STATS_DEFAULT_LOOKBACK_DAYS. See
    app.services.search_query_details_sync_service."""
    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для магазина не заданы ключи Ozon Seller API")

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.OZON_SEARCH_QUERY_STATISTICS_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(db, action="sync_started", user_id=user.id, store_id=ctx.store_id, target_type="sync_run", target_id=run.id)
    db.commit()

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)

    background_tasks.add_task(
        _run_search_query_details_sync, run.id, ctx.store_id, client_id, api_key, date_from, date_to
    )
    return _serialize(run)


def _run_order_daily_statistics_sync(
    run_id: str,
    store_id: str,
    client_id: str,
    api_key: str,
    date_from: date | None,
    date_to: date | None,
) -> None:
    """Runs in a FastAPI BackgroundTask, own SessionLocal() — see
    app.services.order_daily_sync_service."""
    db = SessionLocal()
    error_message = None
    try:
        run = db.get(SyncRun, run_id)
        try:
            with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
                outcome = sync_order_daily_statistics(db, store_id=store_id, client=client, date_from=date_from, date_to=date_to)
            run.items_fetched = outcome.fetched
            run.items_created = outcome.created
            run.items_skipped_duplicate = outcome.updated
            error_message = "; ".join(outcome.errors[:20]) if outcome.errors else None
            run.status = SyncStatus.SUCCESS if not outcome.errors else (
                SyncStatus.PARTIAL if (outcome.created or outcome.updated) else SyncStatus.FAILED
            )
        except OzonAuthError as exc:
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except OzonAPIError as exc:
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except Exception as exc:  # a SyncRun must never be left stuck "running" forever
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = f"Внутренняя ошибка: {exc}"
            logger.exception("Заказы: непредвиденная ошибка автосинхронизации, store_id=%s", store_id)

        run.finished_at = datetime.now(timezone.utc)
        run.error_message = error_message
        try:
            record_audit(
                db, action="sync_finished", store_id=store_id, target_type="sync_run", target_id=run.id,
                result="success" if run.status == SyncStatus.SUCCESS else "failure", message=error_message,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Заказы: не удалось сохранить результат синхронизации, store_id=%s", store_id)
            run.status = SyncStatus.FAILED
            run.error_message = error_message
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@router.post("/ozon-orders")
def sync_ozon_orders(
    background_tasks: BackgroundTasks,
    date_from: date | None = None,
    date_to: date | None = None,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Triggers an automatic pull of daily order/revenue/buyout/cancellation
    statistics from Ozon Seller API's FBO/FBS postings — the "РНП" data
    source. UNCONFIRMED beyond what backend/scripts/debug_orders_finance_api.py
    has verified so far (see app.services.order_daily_sync_service's own
    docstring for exactly what that covers and what it doesn't). Runs in the
    background and returns immediately with a SyncRun the frontend polls via
    GET /runs. date_from/date_to default to the last N days per
    ORDER_STATS_DEFAULT_LOOKBACK_DAYS."""
    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для магазина не заданы ключи Ozon Seller API")

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.OZON_ORDERS_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(db, action="sync_started", user_id=user.id, store_id=ctx.store_id, target_type="sync_run", target_id=run.id)
    db.commit()

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)

    background_tasks.add_task(
        _run_order_daily_statistics_sync, run.id, ctx.store_id, client_id, api_key, date_from, date_to
    )
    return _serialize(run)


def _run_product_analytics_daily_statistics_sync(
    run_id: str,
    store_id: str,
    client_id: str,
    api_key: str,
    date_from: date | None,
    date_to: date | None,
) -> None:
    """Runs in a FastAPI BackgroundTask, own SessionLocal() — see
    app.services.product_analytics_daily_sync_service."""
    db = SessionLocal()
    error_message = None
    try:
        run = db.get(SyncRun, run_id)
        try:
            with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
                outcome = sync_product_analytics_daily_statistics(db, store_id=store_id, client=client, date_from=date_from, date_to=date_to)
            run.items_fetched = outcome.fetched
            run.items_created = outcome.created
            run.items_skipped_duplicate = outcome.updated
            error_message = "; ".join(outcome.errors[:20]) if outcome.errors else None
            run.status = SyncStatus.SUCCESS if not outcome.errors else (
                SyncStatus.PARTIAL if (outcome.created or outcome.updated) else SyncStatus.FAILED
            )
        except OzonAuthError as exc:
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except OzonAPIError as exc:
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except Exception as exc:  # a SyncRun must never be left stuck "running" forever
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = f"Внутренняя ошибка: {exc}"
            logger.exception("Аналитика товаров: непредвиденная ошибка автосинхронизации, store_id=%s", store_id)

        run.finished_at = datetime.now(timezone.utc)
        run.error_message = error_message
        try:
            record_audit(
                db, action="sync_finished", store_id=store_id, target_type="sync_run", target_id=run.id,
                result="success" if run.status == SyncStatus.SUCCESS else "failure", message=error_message,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Аналитика товаров: не удалось сохранить результат синхронизации, store_id=%s", store_id)
            run.status = SyncStatus.FAILED
            run.error_message = error_message
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@router.post("/ozon-product-analytics")
def sync_ozon_product_analytics(
    background_tasks: BackgroundTasks,
    date_from: date | None = None,
    date_to: date | None = None,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Triggers an automatic pull of per-product daily funnel statistics
    (views/cart adds/conversion/position on the product card) from Ozon
    Seller API's POST /v1/analytics/data — see
    app.services.product_analytics_daily_sync_service's own docstring for
    the confirmed contract. Requires the connected Ozon account to have a
    Premium Plus/Premium Pro subscription; without it, this will simply fail
    with whatever error Ozon returns (surfaced on the SyncRun), not silently
    degrade. Runs in the background and returns immediately with a SyncRun
    the frontend polls via GET /runs. date_from/date_to default to the last
    N days per PRODUCT_ANALYTICS_STATS_DEFAULT_LOOKBACK_DAYS."""
    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для магазина не заданы ключи Ozon Seller API")

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.OZON_ANALYTICS_DATA_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(db, action="sync_started", user_id=user.id, store_id=ctx.store_id, target_type="sync_run", target_id=run.id)
    db.commit()

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)

    background_tasks.add_task(
        _run_product_analytics_daily_statistics_sync, run.id, ctx.store_id, client_id, api_key, date_from, date_to
    )
    return _serialize(run)


def _run_cash_flow_statement_sync(
    run_id: str,
    store_id: str,
    client_id: str,
    api_key: str,
    date_from: date | None,
    date_to: date | None,
) -> None:
    """Runs in a FastAPI BackgroundTask, own SessionLocal() — see
    app.services.cash_flow_statement_sync_service."""
    db = SessionLocal()
    error_message = None
    try:
        run = db.get(SyncRun, run_id)
        try:
            with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
                outcome = sync_cash_flow_statement_periods(db, store_id=store_id, client=client, date_from=date_from, date_to=date_to)
            run.items_fetched = outcome.fetched
            run.items_created = outcome.created
            run.items_skipped_duplicate = outcome.updated
            error_message = "; ".join(outcome.errors[:20]) if outcome.errors else None
            run.status = SyncStatus.SUCCESS if not outcome.errors else (
                SyncStatus.PARTIAL if (outcome.created or outcome.updated) else SyncStatus.FAILED
            )
        except OzonAuthError as exc:
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except OzonAPIError as exc:
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except Exception as exc:  # a SyncRun must never be left stuck "running" forever
            db.rollback()
            run.status = SyncStatus.FAILED
            error_message = f"Внутренняя ошибка: {exc}"
            logger.exception("ДДС: непредвиденная ошибка автосинхронизации, store_id=%s", store_id)

        run.finished_at = datetime.now(timezone.utc)
        run.error_message = error_message
        try:
            record_audit(
                db, action="sync_finished", store_id=store_id, target_type="sync_run", target_id=run.id,
                result="success" if run.status == SyncStatus.SUCCESS else "failure", message=error_message,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("ДДС: не удалось сохранить результат синхронизации, store_id=%s", store_id)
            run.status = SyncStatus.FAILED
            run.error_message = error_message
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@router.post("/ozon-cash-flow-statement")
def sync_ozon_cash_flow_statement(
    background_tasks: BackgroundTasks,
    date_from: date | None = None,
    date_to: date | None = None,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Triggers an automatic pull of cash-flow-statement periods (logistics/
    services breakdown for the Дашборд's Логистика/Услуги blocks) from Ozon
    Seller API's POST /v1/finance/cash-flow-statement/list — see
    app.models.cash_flow_statement_period.CashFlowStatementPeriod's own
    docstring for the confirmed contract. Ozon returns its OWN fixed ~weekly
    periods regardless of date_from/date_to — these only bound which of
    Ozon's periods get requested, not the granularity of what comes back.
    Runs in the background and returns immediately with a SyncRun the
    frontend polls via GET /runs. date_from/date_to default to the last N
    days per CASH_FLOW_STATEMENT_DEFAULT_LOOKBACK_DAYS."""
    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для магазина не заданы ключи Ozon Seller API")

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.OZON_CASH_FLOW_STATEMENT_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(db, action="sync_started", user_id=user.id, store_id=ctx.store_id, target_type="sync_run", target_id=run.id)
    db.commit()

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)

    background_tasks.add_task(_run_cash_flow_statement_sync, run.id, ctx.store_id, client_id, api_key, date_from, date_to)
    return _serialize(run)


@router.post("/ozon-rating-summary")
def sync_ozon_rating_summary(
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Pulls the store-wide «Локализация» % (share of local sales) from
    Ozon Seller API's POST /v1/rating/summary — the "Итого" row's own
    Локализация tile on «РНП Товары» (app.services.product_planner_service).
    A single fast call with no pagination/async-report flow, so this runs
    synchronously (like sync_ozon_advertising_campaigns above), not via
    BackgroundTasks. See app.models.store_rating_summary.StoreRatingSummary's
    own docstring for the confirmed contract, including why this can only
    ever be account-wide, never per-product."""
    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для магазина не заданы ключи Ozon Seller API")

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.OZON_RATING_SUMMARY_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(db, action="sync_started", user_id=user.id, store_id=ctx.store_id, target_type="sync_run", target_id=run.id)
    db.commit()

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)

    fetched = created = updated = 0
    error_message = None
    try:
        with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
            data = client.get_rating_summary()
        fetched = 1
        localization_index = data.get("localization_index") or {}
        pct = localization_index.get("localization_percentage")
        calc_date_raw = localization_index.get("calculation_date")
        calc_date = datetime.fromisoformat(calc_date_raw.replace("Z", "+00:00")) if calc_date_raw else None

        summary = db.query(StoreRatingSummary).filter(StoreRatingSummary.store_id == ctx.store_id).first()
        if not summary:
            summary = StoreRatingSummary(store_id=ctx.store_id)
            db.add(summary)
            created = 1
        else:
            updated = 1
        summary.localization_pct = pct
        summary.localization_calculation_date = calc_date
        summary.fetched_at = datetime.now(timezone.utc)

        if pct is None:
            run.status = SyncStatus.PARTIAL
            error_message = (
                "Ozon не вернул localization_index — вероятно, за последние 14 дней не было продаж."
            )
        else:
            run.status = SyncStatus.SUCCESS
    except OzonAuthError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except OzonFeatureUnavailable as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except OzonAPIError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = fetched
    run.items_created = created
    run.items_skipped_duplicate = updated
    run.error_message = error_message
    db.flush()
    record_audit(
        db,
        action="sync_finished",
        user_id=user.id,
        store_id=ctx.store_id,
        target_type="sync_run",
        target_id=run.id,
        result="success" if run.status == SyncStatus.SUCCESS else "failure",
        message=error_message,
    )
    db.commit()
    return _serialize(run)
