import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.core.config import get_settings
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
from app.services.order_daily_sync_service import (
    commission_missing_units_note,
    fetched_by_schema_note,
    find_blocking_running_sync,
    skipped_no_process_date_note,
    sync_order_daily_statistics,
)
from app.services.ozon.client import OzonCredentials as OzonClientCredentials
from app.services.ozon.client import OzonSellerClient
from app.services.ozon.exceptions import OzonAPIError, OzonAuthError, OzonFeatureUnavailable
from app.services.product_analytics_daily_sync_service import sync_product_analytics_daily_statistics
from app.services.product_catalog_sync_service import sync_product_catalog
from app.services.accrual_daily_sync_service import sync_accrual_daily_statistic, sync_recent_accrual_days
from app.services.realization_report_sync_service import sync_missing_realization_reports, sync_realization_report_month
from app.services.search_query_details_sync_service import sync_search_query_details
from app.services.ozon_performance.client import OzonPerformanceClient
from app.services.ozon_performance.client import PerformanceCredentials as OzonPerfCredentials
from app.services.ozon_performance.exceptions import (
    OzonPerformanceAPIError,
    OzonPerformanceAuthError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stores/{store_id}/sync", tags=["sync"])


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
    Ozon — thin HTTP wrapper around app.services.product_catalog_sync_
    service.sync_product_catalog(), which app.services.product_catalog_
    scheduler also calls automatically once a day. See that service
    module's own docstring for the full sync contract (sku=0 fallback,
    duplicate-row merge, ProductPriceDailySnapshot capture)."""
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

    with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
        outcome = sync_product_catalog(db, store_id=ctx.store_id, client=client)

    error_message = "; ".join(outcome.errors[:20]) if outcome.errors else None
    if outcome.hard_failure:
        run.status = SyncStatus.FAILED
    else:
        run.status = SyncStatus.SUCCESS if not outcome.errors else (
            SyncStatus.PARTIAL if (outcome.created or outcome.updated) else SyncStatus.FAILED
        )

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = outcome.fetched
    run.items_created = outcome.created
    run.items_skipped_duplicate = outcome.updated  # "duplicates" here means products already known and refreshed
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
            notes = list(outcome.errors[:20])
            skipped_note = skipped_no_process_date_note(outcome)
            if skipped_note:
                notes.append(skipped_note)
            commission_note = commission_missing_units_note(outcome)
            if commission_note:
                notes.append(commission_note)
            schema_note = fetched_by_schema_note(outcome)
            if schema_note:
                notes.append(schema_note)
            error_message = "; ".join(notes) if notes else None
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

    if find_blocking_running_sync(db, store_id=ctx.store_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Синхронизация заказов для этого магазина уже выполняется — дождитесь её завершения "
            "(см. «Журнал синхронизаций»). Повторный запуск сейчас рискует тем, что более старый прогон "
            "допишется позже и перезапишет более свежие данные.",
        )

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


@router.post("/ozon-realization-report")
def sync_ozon_realization_report(
    year: int | None = None,
    month: int | None = None,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Pulls Ozon's official monthly settlement report (POST /v2/finance/
    realization, "Отчёт о реализации товаров") — see
    app.models.realization_report_month.RealizationReportMonth's own
    docstring for the confirmed request shape and why this can only ever
    succeed for an already-CLOSED calendar month (Ozon itself answers 404
    "Report was not found" for the current one — treated here as an
    ordinary, expected outcome, not a failure).

    Pass year+month to sync one specific month (e.g. re-fetch a known-good
    one). Omit both to try every one of the last REALIZATION_REPORT_
    BACKFILL_MONTHS closed months this store doesn't already have archived
    — the same thing the daily scheduler does, safe to click any time
    (idempotent, skips whatever's already archived). A single fast call
    per month with no pagination, so this runs synchronously (like
    sync_ozon_rating_summary above), not via BackgroundTasks."""
    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для магазина не заданы ключи Ozon Seller API")
    if (year is None) != (month is None):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Укажите и year, и month, либо ни одного из них")

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.OZON_REALIZATION_REPORT_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(db, action="sync_started", user_id=user.id, store_id=ctx.store_id, target_type="sync_run", target_id=run.id)
    db.commit()

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)

    error_message = None
    try:
        with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
            if year is not None and month is not None:
                outcome = sync_realization_report_month(db, store_id=ctx.store_id, client=client, year=year, month=month)
                results = [(year, month, outcome)]
            else:
                settings = get_settings()
                results = sync_missing_realization_reports(
                    db, store_id=ctx.store_id, client=client, backfill_months=settings.REALIZATION_REPORT_BACKFILL_MONTHS
                )

        fetched = sum(1 for _, _, o in results if o.fetched)
        created = sum(1 for _, _, o in results if o.fetched and o.created)
        updated = fetched - created
        not_yet = [f"{y}-{m:02d}" for y, m, o in results if o.not_yet_available]
        real_errors = [f"{y}-{m:02d}: {o.error}" for y, m, o in results if o.error and not o.not_yet_available]

        run.items_fetched = fetched
        run.items_created = created
        run.items_skipped_duplicate = updated

        notes = list(real_errors[:20])
        if not_yet:
            notes.append(f"Ещё не готов отчёт Ozon за: {', '.join(not_yet)} (обычная ситуация — месяц ещё не закрылся)")
        if not results:
            notes.append("Нечего запрашивать — все месяцы в пределах бэкфилла уже архивированы")
        error_message = "; ".join(notes) if notes else None
        run.status = SyncStatus.SUCCESS if not real_errors else (
            SyncStatus.PARTIAL if fetched else SyncStatus.FAILED
        )
    except OzonAuthError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except OzonAPIError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)

    run.finished_at = datetime.now(timezone.utc)
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


@router.post("/ozon-accrual-daily")
def sync_ozon_accrual_daily(
    date_from: date | None = None,
    date_to: date | None = None,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Pulls Ozon's TRUE DAILY accrual total (POST /v1/finance/accrual/
    by-day) — see app.models.accrual_daily_statistic.AccrualDailyStatistic's
    own docstring for the confirmed contract: summing total_amount.amount
    across a day's records matched Ozon's own cabinet total for that day
    to the kopeck. Only the whole-day total and a coarse accrued_category
    breakdown are stored — NOT yet a "Комиссия Ozon"-specific figure (see
    that model's own docstring for why).

    Omit both dates to re-sync the last ACCRUAL_DAILY_TRAILING_DAYS days
    (the same window the nightly scheduler covers — Ozon can revise a
    recent day's accruals after the fact, so this always re-fetches even
    already-archived days in that window, unlike the realization-report
    manual trigger). Pass date_from/date_to to sync a specific range
    instead (e.g. a historical backfill) — inclusive, day by day. A single
    fast call per day with no pagination beyond what Ozon itself paginates
    internally, so this runs synchronously (like sync_ozon_rating_summary
    above), not via BackgroundTasks."""
    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для магазина не заданы ключи Ozon Seller API")
    if (date_from is None) != (date_to is None):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Укажите и date_from, и date_to, либо ни одного из них")

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.OZON_ACCRUAL_DAILY_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    record_audit(db, action="sync_started", user_id=user.id, store_id=ctx.store_id, target_type="sync_run", target_id=run.id)
    db.commit()

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)

    error_message = None
    try:
        with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
            if date_from is not None and date_to is not None:
                results = []
                day = date_from
                while day <= date_to:
                    results.append((day, sync_accrual_daily_statistic(db, store_id=ctx.store_id, client=client, day=day)))
                    day += timedelta(days=1)
            else:
                settings = get_settings()
                results = sync_recent_accrual_days(db, store_id=ctx.store_id, client=client, days=settings.ACCRUAL_DAILY_TRAILING_DAYS)

        fetched = sum(1 for _, o in results if o.fetched)
        created = sum(1 for _, o in results if o.fetched and o.created)
        updated = fetched - created
        real_errors = [f"{d.isoformat()}: {o.error}" for d, o in results if o.error]

        run.items_fetched = fetched
        run.items_created = created
        run.items_skipped_duplicate = updated
        error_message = "; ".join(real_errors[:20]) if real_errors else None
        run.status = SyncStatus.SUCCESS if not real_errors else (
            SyncStatus.PARTIAL if fetched else SyncStatus.FAILED
        )
    except OzonAuthError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except OzonAPIError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)

    run.finished_at = datetime.now(timezone.utc)
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
