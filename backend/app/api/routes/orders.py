from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.order_daily_statistic import OrderDailyStatistic
from app.models.product import Product
from app.models.product_order_daily_statistic import ProductOrderDailyStatistic
from app.schemas.order_daily import (
    OrderDailyStatisticListResponse,
    OrderDailyStatisticOut,
    ProductOrderDailyStatisticListResponse,
    ProductOrderDailyStatisticOut,
)

router = APIRouter(prefix="/api/stores/{store_id}/orders", tags=["orders"])


@router.get("/daily-statistics", response_model=OrderDailyStatisticListResponse)
def list_order_daily_statistics(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    date_from: date | None = None,
    date_to: date | None = None,
) -> OrderDailyStatisticListResponse:
    """Automatically-collected daily order statistics from Ozon Seller API
    postings (FBO/FBS) — see app.services.order_daily_sync_service. Rows
    for FBO and FBS are returned separately (not merged) — the "РНП" page
    sums them client-side for a combined day total, same convention as the
    Реклама page's auto vs. manual advertising sources."""
    stmt = select(OrderDailyStatistic).where(OrderDailyStatistic.store_id == ctx.store_id)
    if date_from:
        stmt = stmt.where(OrderDailyStatistic.date >= date_from)
    if date_to:
        stmt = stmt.where(OrderDailyStatistic.date <= date_to)

    rows = db.scalars(stmt.order_by(OrderDailyStatistic.date.desc())).all()
    items = [OrderDailyStatisticOut.model_validate(r) for r in rows]
    return OrderDailyStatisticListResponse(items=items, total=len(items))


@router.get("/product-daily-statistics", response_model=ProductOrderDailyStatisticListResponse)
def list_product_order_daily_statistics(
    product_id: str,
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    date_from: date | None = None,
    date_to: date | None = None,
) -> ProductOrderDailyStatisticListResponse:
    """Same source and sync as /daily-statistics above (see
    order_daily_sync_service.aggregate_postings_by_sku_and_day), sliced by
    one product's ozon_sku instead of summed across the whole store — the
    product detail page's "Продажи" tab. product_id is resolved and
    store-checked here (never a bare ozon_sku trusted from the client), so a
    product_id from another store simply 404s. Rows for FBO and FBS are
    returned separately, same convention as /daily-statistics."""
    product = db.get(Product, product_id)
    if not product or product.store_id != ctx.store_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    stmt = select(ProductOrderDailyStatistic).where(
        ProductOrderDailyStatistic.store_id == ctx.store_id,
        ProductOrderDailyStatistic.ozon_sku == product.ozon_sku,
    )
    if date_from:
        stmt = stmt.where(ProductOrderDailyStatistic.date >= date_from)
    if date_to:
        stmt = stmt.where(ProductOrderDailyStatistic.date <= date_to)

    rows = db.scalars(stmt.order_by(ProductOrderDailyStatistic.date.desc())).all()
    items = [ProductOrderDailyStatisticOut.model_validate(r) for r in rows]
    return ProductOrderDailyStatisticListResponse(items=items, total=len(items))
