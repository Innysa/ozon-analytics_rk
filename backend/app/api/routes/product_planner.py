from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.product import Product
from app.models.product_monthly_plan import ProductMonthlyPlan
from app.schemas.product_planner import ProductMonthlyPlanIn, ProductPlannerOut, SuggestedPlan
from app.services.product_planner_service import compute_product_planner, suggest_plan

router = APIRouter(prefix="/api/stores/{store_id}/product-planner", tags=["product-planner"])


def _resolved_year_month(year: int | None, month: int | None) -> tuple[int, int]:
    if year is not None and month is not None:
        return year, month
    today = datetime.now(timezone.utc).date()
    return year or today.year, month or today.month


@router.get("", response_model=ProductPlannerOut)
def get_product_planner(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    year: int | None = None,
    month: int | None = None,
) -> ProductPlannerOut:
    """«РНП Товары» — план/прогноз/факт по каждому товару магазина за
    календарный месяц. year/month по умолчанию — текущий месяц. See
    app.services.product_planner_service's own module docstring for the
    full computation and what «Локализация»/«Прогноз»/«Хватит на» mean
    here specifically."""
    resolved_year, resolved_month = _resolved_year_month(year, month)
    return compute_product_planner(db, store_id=ctx.store_id, year=resolved_year, month=resolved_month)


@router.put("/products/{product_id}/plan", response_model=ProductPlannerOut)
def set_product_monthly_plan(
    product_id: str,
    payload: ProductMonthlyPlanIn,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    year: int | None = None,
    month: int | None = None,
) -> ProductPlannerOut:
    """Upserts the manual plan for one product/month — the only
    user-editable input on this page. Returns the recomputed planner so the
    frontend can refresh from one response instead of a second round-trip."""
    product = db.get(Product, product_id)
    if not product or product.store_id != ctx.store_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    resolved_year, resolved_month = _resolved_year_month(year, month)
    plan = (
        db.query(ProductMonthlyPlan)
        .filter(
            ProductMonthlyPlan.store_id == ctx.store_id,
            ProductMonthlyPlan.product_id == product_id,
            ProductMonthlyPlan.year == resolved_year,
            ProductMonthlyPlan.month == resolved_month,
        )
        .first()
    )
    if not plan:
        plan = ProductMonthlyPlan(store_id=ctx.store_id, product_id=product_id, year=resolved_year, month=resolved_month)
        db.add(plan)

    plan.plan_orders_units = payload.plan_orders_units
    plan.plan_orders_sum_rub = payload.plan_orders_sum_rub
    plan.plan_buyouts_units = payload.plan_buyouts_units
    plan.plan_buyouts_sum_rub = payload.plan_buyouts_sum_rub
    plan.plan_ad_budget_rub = payload.plan_ad_budget_rub
    plan.plan_profit_rub = payload.plan_profit_rub
    db.commit()

    return compute_product_planner(db, store_id=ctx.store_id, year=resolved_year, month=resolved_month)


@router.get("/products/{product_id}/suggest-plan", response_model=SuggestedPlan)
def get_suggested_plan(
    product_id: str,
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    year: int | None = None,
    month: int | None = None,
) -> SuggestedPlan:
    """Preview only — never writes a ProductMonthlyPlan itself. The seller
    reviews the suggestion and, if they want it, submits it via PUT
    .../plan like any other manually-entered plan (see this module's own
    docstring: "предложить план" is opt-in, not an automatic recalculation)."""
    product = db.get(Product, product_id)
    if not product or product.store_id != ctx.store_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    resolved_year, resolved_month = _resolved_year_month(year, month)
    return suggest_plan(db, store_id=ctx.store_id, product_id=product_id, year=resolved_year, month=resolved_month)
