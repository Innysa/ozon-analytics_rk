from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.product import Product
from app.models.product_card_statistic import ProductCardStatistic
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.models.user import User
from app.schemas.common import ImportSummary
from app.schemas.product_analytics import (
    ProductCardAnalyticsOut,
    ProductCardStatisticListResponse,
    ProductCardStatisticOut,
)
from app.services.audit import record_audit
from app.services.product_analytics_import import import_product_card_statistics_from_file
from app.services.product_analytics_service import compute_product_card_analytics

router = APIRouter(prefix="/api/stores/{store_id}/product-analytics", tags=["product-analytics"])

_ALLOWED_EXT = (".csv", ".xlsx")


@router.post("/upload", response_model=ImportSummary)
async def upload_product_analytics(
    file: UploadFile,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ImportSummary:
    """Imports Ozon's own "Аналитика → Товары" export (CSV/XLSX, sheet "По
    товарам"): real per-day sales-funnel/conversion/order/stock/rating data
    for one or more products. See app.services.product_analytics_import."""
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

    result = import_product_card_statistics_from_file(db, store_id=ctx.store_id, filename=file.filename, content=content)

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = result.fetched
    run.items_created = result.created
    run.items_skipped_duplicate = result.skipped_duplicate
    run.status = SyncStatus.SUCCESS if not result.errors else (SyncStatus.PARTIAL if result.created else SyncStatus.FAILED)
    run.error_message = "; ".join(result.errors[:20]) if result.errors else None

    record_audit(
        db,
        action="product_analytics_uploaded",
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


@router.get("", response_model=ProductCardStatisticListResponse)
def list_product_analytics(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    product_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ProductCardStatisticListResponse:
    stmt = select(ProductCardStatistic).where(ProductCardStatistic.store_id == ctx.store_id)
    if product_id:
        stmt = stmt.where(ProductCardStatistic.product_id == product_id)
    if date_from:
        stmt = stmt.where(ProductCardStatistic.date >= date_from)
    if date_to:
        stmt = stmt.where(ProductCardStatistic.date <= date_to)

    rows = db.scalars(stmt.order_by(ProductCardStatistic.date.desc())).all()

    product_ids = {r.product_id for r in rows if r.product_id}
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_(product_ids)).all()} if product_ids else {}

    items = [
        ProductCardStatisticOut(
            id=r.id,
            product_id=r.product_id,
            product_name=products[r.product_id].name if r.product_id in products else None,
            ozon_sku=r.ozon_sku,
            offer_id=r.offer_id,
            brand=r.brand,
            category_l1=r.category_l1,
            fulfillment_scheme=r.fulfillment_scheme,
            date=r.date,
            impressions_total=r.impressions_total,
            impressions_search_catalog=r.impressions_search_catalog,
            card_visits=r.card_visits,
            cart_adds_total=r.cart_adds_total,
            ordered_units=r.ordered_units,
            delivered_units=r.delivered_units,
            bought_out_units=r.bought_out_units,
            cancelled_units_by_order_date=r.cancelled_units_by_order_date,
            returned_units_by_order_date=r.returned_units_by_order_date,
            ordered_sum_actual_price_rub=r.ordered_sum_actual_price_rub,
            search_catalog_position_ozon=r.search_catalog_position_ozon,
            conv_impression_to_order_pct_ozon=r.conv_impression_to_order_pct_ozon,
            conv_to_cart_total_pct_ozon=r.conv_to_cart_total_pct_ozon,
            conv_cart_to_order_pct_ozon=r.conv_cart_to_order_pct_ozon,
            conv_order_to_buyout_pct_ozon=r.conv_order_to_buyout_pct_ozon,
            avg_price_rub=r.avg_price_rub,
            price_index_label_ozon=r.price_index_label_ozon,
            drr_pct_ozon=r.drr_pct_ozon,
            stock_end_of_period=r.stock_end_of_period,
            reviews_count=r.reviews_count,
            rating=r.rating,
        )
        for r in rows
    ]
    return ProductCardStatisticListResponse(items=items, total=len(items))


@router.get("/summary", response_model=ProductCardAnalyticsOut)
def product_analytics_summary(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    product_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ProductCardAnalyticsOut:
    return compute_product_card_analytics(db, store_id=ctx.store_id, product_id=product_id, date_from=date_from, date_to=date_to)
