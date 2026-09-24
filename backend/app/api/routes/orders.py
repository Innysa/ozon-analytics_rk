from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.core.moscow_time import moscow_today
from app.db.session import get_db
from app.models.accrual_daily_statistic import AccrualDailyStatistic
from app.models.membership import StoreRole
from app.models.order_daily_statistic import OrderDailyStatistic
from app.models.product import Product
from app.models.product_analytics_daily_statistic import ProductAnalyticsDailyStatistic
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
    already-whole-day accrual figure to both rows would double it.

    ordered_units is overridden the SAME way (applied once per date, zeroed
    on the day's other row), with the store's ProductAnalyticsDailyStatistic
    (Ozon Analytics API funnel) rows summed across every SKU for that date —
    **ПЕРЕКЛЮЧЕНО 2026-09-24**, same reasoning and same narrow scope
    (units only, never ordered_sum_rub — unconfirmed price basis) as the
    identical switch made on «РНП Товары», see app.services.
    product_planner_service's own module docstring for the full story. Only
    applied to a date the funnel has actually synced AND that's already
    CLOSED (strictly before today in Moscow time, ИСПРАВЛЕНО 2026-09-24 —
    see moscow_today()'s own docstring for the real account that surfaced
    this) — a date with no funnel rows, or today itself, keeps the
    postings figure unchanged."""
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

    funnel_stmt = select(
        ProductAnalyticsDailyStatistic.date, func.sum(ProductAnalyticsDailyStatistic.ordered_units)
    ).where(
        ProductAnalyticsDailyStatistic.store_id == ctx.store_id,
        # Никогда для СЕГОДНЯ — см. product_planner_service's module
        # docstring, «ИСПРАВЛЕНО 2026-09-24»: сегодняшняя строка воронки
        # (если уже есть) отражает лишь то, что Ozon успел посчитать к
        # последней ночной попытке, а не весь ещё идущий день.
        ProductAnalyticsDailyStatistic.date < moscow_today(),
    )
    if date_from:
        funnel_stmt = funnel_stmt.where(ProductAnalyticsDailyStatistic.date >= date_from)
    if date_to:
        funnel_stmt = funnel_stmt.where(ProductAnalyticsDailyStatistic.date <= date_to)
    funnel_units_by_date = {
        d: int(units) for d, units in db.execute(funnel_stmt.group_by(ProductAnalyticsDailyStatistic.date)).all()
    }

    applied_commission_dates: set[date] = set()
    applied_orders_dates: set[date] = set()
    items = []
    for r in rows:
        item = OrderDailyStatisticOut.model_validate(r)
        update: dict = {}
        if r.date in accrual_by_date:
            update["commission_rub"] = accrual_by_date[r.date] if r.date not in applied_commission_dates else 0.0
            applied_commission_dates.add(r.date)
        if r.date in funnel_units_by_date:
            update["ordered_units"] = funnel_units_by_date[r.date] if r.date not in applied_orders_dates else 0
            applied_orders_dates.add(r.date)
        if update:
            item = item.model_copy(update=update)
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
