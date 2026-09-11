from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.product import Product
from app.models.product_monthly_plan import ProductMonthlyPlan
from app.schemas.product_planner import BulkPlanIn, ProductMonthlyPlanIn, ProductPlannerOut, SuggestedPlan
from app.services.product_planner_service import compute_product_planner, suggest_plan

router = APIRouter(prefix="/api/stores/{store_id}/product-planner", tags=["product-planner"])


def _resolved_year_month(year: int | None, month: int | None) -> tuple[int, int]:
    if year is not None and month is not None:
        return year, month
    today = datetime.now(timezone.utc).date()
    return year or today.year, month or today.month


def _upsert_plan(db: Session, *, store_id: str, product_id: str, year: int, month: int, fields: dict) -> None:
    """Shared by the single-product and bulk plan-saving routes. PATCH
    semantics: only the keys actually present in `fields` are written —
    a field the caller never mentioned is left untouched in the DB, not
    reset to null. This matters for the mass plan-entry screen, which
    doesn't show/edit plan_orders_sum_rub at all (see BulkPlanTable on the
    frontend) — saving a row there must not silently wipe a rubles plan
    entered earlier via the per-product card. Callers build `fields` via
    `payload.model_dump(exclude_unset=True)` so a field literally absent
    from the request body is excluded, while one explicitly sent as null
    still clears it. Does not commit; callers batch their own commit (one
    for a single save, one for the whole bulk table)."""
    plan = (
        db.query(ProductMonthlyPlan)
        .filter(
            ProductMonthlyPlan.store_id == store_id,
            ProductMonthlyPlan.product_id == product_id,
            ProductMonthlyPlan.year == year,
            ProductMonthlyPlan.month == month,
        )
        .first()
    )
    if not plan:
        plan = ProductMonthlyPlan(store_id=store_id, product_id=product_id, year=year, month=month)
        db.add(plan)

    for key, value in fields.items():
        setattr(plan, key, value)


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
    """Upserts the manual plan for one product/month (Заказы/Рекламный
    бюджет only — see ProductMonthlyPlan's own docstring for why Выкупы/
    Прибыль are never planned). Returns the recomputed planner so the
    frontend can refresh from one response instead of a second round-trip."""
    product = db.get(Product, product_id)
    if not product or product.store_id != ctx.store_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    resolved_year, resolved_month = _resolved_year_month(year, month)
    _upsert_plan(
        db, store_id=ctx.store_id, product_id=product_id, year=resolved_year, month=resolved_month,
        fields=payload.model_dump(exclude_unset=True),
    )
    db.commit()

    return compute_product_planner(db, store_id=ctx.store_id, year=resolved_year, month=resolved_month)


@router.put("/plans/bulk", response_model=ProductPlannerOut)
def bulk_set_product_monthly_plans(
    payload: BulkPlanIn,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    year: int | None = None,
    month: int | None = None,
) -> ProductPlannerOut:
    """Saves the plan for MANY products in one request — the mass plan-entry
    screen on the «РНП Товары» page (one table, one save button, instead of
    opening every product's card and calling PUT .../plan one at a time).
    Any product_id not belonging to this store is skipped rather than
    failing the whole batch — same reasoning as bulk_generate_drafts in
    reviews.py: one bad row shouldn't block every other row's save."""
    resolved_year, resolved_month = _resolved_year_month(year, month)
    store_product_ids = {
        p.id for p in db.query(Product.id).filter(Product.store_id == ctx.store_id).all()
    }
    for entry in payload.entries:
        if entry.product_id not in store_product_ids:
            continue
        _upsert_plan(
            db, store_id=ctx.store_id, product_id=entry.product_id, year=resolved_year, month=resolved_month,
            fields=entry.model_dump(exclude_unset=True, exclude={"product_id"}),
        )
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
