from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.db.session import get_db
from app.models.advertising_campaign import AdvertisingCampaign
from app.models.advertising_statistic import AdvertisingStatistic
from app.models.membership import StoreRole
from app.models.product import Product
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.models.user import User
from app.schemas.advertising import (
    AdvertisingAnalyticsOut,
    AdvertisingCampaignOut,
    AdvertisingStatisticListResponse,
    AdvertisingStatisticOut,
)
from app.schemas.common import ImportSummary
from app.services.advertising_analytics_service import compute_advertising_analytics
from app.services.advertising_import import import_advertising_statistics_from_file
from app.services.audit import record_audit

router = APIRouter(prefix="/api/stores/{store_id}/advertising", tags=["advertising"])

_ALLOWED_EXT = (".csv", ".xlsx")


@router.get("/campaigns", response_model=list[AdvertisingCampaignOut])
def list_campaigns(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)), db: Session = Depends(get_db)
) -> list[AdvertisingCampaignOut]:
    """Campaign metadata synced from Ozon Performance API (see
    app.services.ozon_performance.client). Returns an empty list — never
    fabricated data — until a sync has actually run for this store."""
    campaigns = db.scalars(
        select(AdvertisingCampaign).where(AdvertisingCampaign.store_id == ctx.store_id).order_by(AdvertisingCampaign.name)
    ).all()
    return [AdvertisingCampaignOut.model_validate(c) for c in campaigns]


@router.post("/statistics/upload", response_model=ImportSummary)
async def upload_statistics(
    file: UploadFile,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ImportSummary:
    """Imports Ozon's own "Продвижение → Статистика" export (CSV/XLSX). This
    is the real, verified path to spend/impressions/clicks/ДРР data — see
    app.services.advertising_import for why the Performance API's async
    statistics-report flow isn't used instead."""
    if not file.filename or not file.filename.lower().endswith(_ALLOWED_EXT):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Поддерживаются только файлы .csv и .xlsx")

    content = await file.read()
    source_type = SyncSourceType.CSV_IMPORT if file.filename.lower().endswith(".csv") else SyncSourceType.XLSX_IMPORT

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=source_type,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    result = import_advertising_statistics_from_file(db, store_id=ctx.store_id, filename=file.filename, content=content)

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = result.fetched
    run.items_created = result.created
    run.items_skipped_duplicate = result.skipped_duplicate
    run.status = SyncStatus.SUCCESS if not result.errors else (SyncStatus.PARTIAL if result.created else SyncStatus.FAILED)
    run.error_message = "; ".join(result.errors[:20]) if result.errors else None

    record_audit(
        db,
        action="advertising_statistics_uploaded",
        user_id=user.id,
        store_id=ctx.store_id,
        target_type="sync_run",
        target_id=run.id,
        result="success" if run.status != SyncStatus.FAILED else "failure",
        message=f"файл {file.filename}: создано {result.created}, дублей {result.skipped_duplicate}",
    )
    db.commit()

    return ImportSummary(
        fetched=result.fetched, created=result.created, skipped_duplicate=result.skipped_duplicate, errors=result.errors
    )


@router.get("/statistics", response_model=AdvertisingStatisticListResponse)
def list_statistics(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    product_id: str | None = None,
    campaign_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> AdvertisingStatisticListResponse:
    stmt = select(AdvertisingStatistic).where(AdvertisingStatistic.store_id == ctx.store_id)
    if product_id:
        stmt = stmt.where(AdvertisingStatistic.product_id == product_id)
    if campaign_id:
        stmt = stmt.where(AdvertisingStatistic.campaign_id == campaign_id)
    if date_from:
        stmt = stmt.where(AdvertisingStatistic.period_end >= date_from)
    if date_to:
        stmt = stmt.where(AdvertisingStatistic.period_start <= date_to)

    rows = db.scalars(stmt.order_by(AdvertisingStatistic.period_start.desc())).all()

    product_ids = {r.product_id for r in rows if r.product_id}
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_(product_ids)).all()} if product_ids else {}
    campaign_ids = {r.campaign_id for r in rows if r.campaign_id}
    campaigns = (
        {c.id: c for c in db.query(AdvertisingCampaign).filter(AdvertisingCampaign.id.in_(campaign_ids)).all()}
        if campaign_ids
        else {}
    )

    items = []
    for r in rows:
        product = products.get(r.product_id)
        campaign = campaigns.get(r.campaign_id)
        spend = float(r.spend_rub)
        sales = float(r.sales_promo_rub) if r.sales_promo_rub is not None else None
        items.append(
            AdvertisingStatisticOut(
                id=r.id,
                product_id=r.product_id,
                product_name=product.name if product else None,
                product_sku=r.ozon_sku,
                campaign_id=r.campaign_id,
                campaign_name=campaign.name if campaign else None,
                ozon_campaign_id=r.ozon_campaign_id,
                ad_tool=r.ad_tool,
                placement=r.placement,
                period_start=r.period_start,
                period_end=r.period_end,
                spend_rub=spend,
                sales_promo_rub=sales,
                units_sold=r.units_sold,
                impressions=r.impressions,
                clicks=r.clicks,
                ctr_pct_ozon=r.ctr_pct_ozon,
                cart_additions=r.cart_additions,
                cart_conversion_pct_ozon=r.cart_conversion_pct_ozon,
                drr_promo_pct_ozon=r.drr_promo_pct_ozon,
                drr_total_pct_ozon=r.drr_total_pct_ozon,
                cost_per_order_rub_ozon=r.cost_per_order_rub_ozon,
                avg_cpc_rub_ozon=r.avg_cpc_rub_ozon,
                drr_calculated_pct=round(spend / sales * 100, 2) if sales else None,
                roas_calculated=round(sales / spend, 3) if sales and spend else None,
            )
        )

    return AdvertisingStatisticListResponse(items=items, total=len(items))


@router.get("/analytics", response_model=AdvertisingAnalyticsOut)
def advertising_analytics(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    product_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> AdvertisingAnalyticsOut:
    return compute_advertising_analytics(db, store_id=ctx.store_id, product_id=product_id, date_from=date_from, date_to=date_to)
