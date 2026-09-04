from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.core.encryption import decrypt_secret
from app.db.session import get_db
from app.models.advertising_campaign import AdvertisingCampaign
from app.models.membership import StoreRole
from app.models.ozon_credentials import OzonCredentials
from app.models.product import Product
from app.models.review import Review, ReviewSource, ReviewStatus
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.models.user import User
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
