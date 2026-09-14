from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.db.session import get_db
from app.models.accrual_daily_statistic import AccrualDailyStatistic
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
    Реклама page's auto vs. manual advertising sources.

    commission_rub is overridden here with AccrualDailyStatistic.
    commission_ozon_rub (CONFIRMED exact against Ozon's own cabinet, see
    that model's own docstring) for any day that's been synced — same
    swap the Dashboard's margin block makes, see dashboard_service.
    _commission_rub_for_period's own docstring. Applied to only ONE of a
    day's rows (FBO/FBS are separate rows, see OrderDailyStatistic's own
    UniqueConstraint) and zeroed on the other, since the "РНП" page sums
    commission_rub across every row for a date client-side — applying the
    already-whole-day accrual figure to both rows would double it."""
    stmt = select(OrderDailyStatistic).where(OrderDailyStatistic.store_id == ctx.store_id)
    if date_from:
        stmt = stmt.where(OrderDailyStatistic.date >= date_from)
    if date_to:
        stmt = stmt.where(OrderDailyStatistic.date <= date_to)

    rows = db.scalars(stmt.order_by(OrderDailyStatistic.date.desc())).all()

    accrual_stmt = select(AccrualDailyStatistic.date, AccrualDailyStatistic.commission_ozon_rub).where(
        AccrualDailyStatistic.store_id == ctx.store_id,
    )
    if date_from:
        accrual_stmt = accrual_stmt.where(AccrualDailyStatistic.date >= date_from)
    if date_to:
        accrual_stmt = accrual_stmt.where(AccrualDailyStatistic.date <= date_to)
    accrual_by_date = {d: float(c) for d, c in db.execute(accrual_stmt).all()}

    applied_dates: set[date] = set()
    items = []
    for r in rows:
        item = OrderDailyStatisticOut.model_validate(r)
        if r.date in accrual_by_date:
            commission = accrual_by_date[r.date] if r.date not in applied_dates else 0.0
            applied_dates.add(r.date)
            item = item.model_copy(update={"commission_rub": commission})
        items.append(item)
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
