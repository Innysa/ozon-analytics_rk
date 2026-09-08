from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.order_daily_statistic import OrderDailyStatistic
from app.schemas.order_daily import OrderDailyStatisticListResponse, OrderDailyStatisticOut

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
