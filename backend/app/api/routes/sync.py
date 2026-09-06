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
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.models.user import User
from app.services.advertising_daily_sync_service import sync_advertising_daily_statistics
from app.services.audit import record_audit
from app.services.ozon.client import OzonCredentials as OzonClientCredentials
from app.services.ozon.client import OzonSellerClient
from app.services.ozon.exceptions import OzonAPIError, OzonAuthError, OzonFeatureUnavailable
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
    in batches for the actual details, since list responses only carry ids."""
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

    existing_by_sku = {
        p.ozon_sku: p for p in db.query(Product).filter(Product.store_id == ctx.store_id).all()
    }

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
        user_id=user.id,
        store_id=ctx.store_id,
        target_type="sync_run",
        target_id=run.id,
        result="success" if run.status == SyncStatus.SUCCESS else "failure",
        message=error_message,
    )
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
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except OzonPerformanceAPIError as exc:
            run.status = SyncStatus.FAILED
            error_message = str(exc)
        except Exception as exc:  # a SyncRun must never be left stuck "running" forever
            run.status = SyncStatus.FAILED
            error_message = f"Внутренняя ошибка: {exc}"
            logger.exception("Реклама: непредвиденная ошибка автосинхронизации статистики, store_id=%s", store_id)

        run.finished_at = datetime.now(timezone.utc)
        run.error_message = error_message
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
