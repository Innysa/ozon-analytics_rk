"""«РНП Товары» — per-product monthly planner (see
app.services.product_planner_service for the full computation and
app.models.product_monthly_plan.ProductMonthlyPlan for the one user-editable
input, the monthly plan itself)."""
from datetime import date

from pydantic import BaseModel


class MetricPlanFactActual(BaseModel):
    """Shared shape for each of the four metric groups on a planner row:
    Заказы, Выкупы, Рекламный бюджет, Прибыль. plan_day_* is always
    plan_month_* / days_in_month (computed, never stored separately — see
    ProductMonthlyPlan's own docstring). Unit fields (*_units) stay None for
    Рекламный бюджет/Прибыль, which are money-only groups on the planner
    page, exactly like AdvertisingDailyStatistic.spend_rub has no unit
    count of its own. plan_day_rub/plan_month_rub/plan_day_units/
    plan_month_units also stay None for Выкупы/Прибыль specifically — not
    because there's nothing to show, but because the user never plans
    those two groups at all (confirmed 2026-09-11); forecast_*/actual_*
    are still populated for them from real order/cost data."""

    plan_day_rub: float | None = None
    plan_month_rub: float | None = None
    forecast_month_rub: float | None = None
    actual_month_rub: float | None = None
    plan_day_units: float | None = None  # plan_month_units / days_in_month — naturally fractional (e.g. 3.3 шт/день)
    plan_month_units: int | None = None
    forecast_month_units: int | None = None
    actual_month_units: int | None = None


class DailyBreakdownEntry(BaseModel):
    date: date
    orders_sum_rub: float
    orders_units: int
    buyouts_sum_rub: float
    buyouts_units: int
    ad_spend_rub: float
    profit_rub: float | None = None  # None when the product's cost price isn't set


class ProductPlannerRow(BaseModel):
    """product_id is None only for the aggregated "Итого" row at the top of
    the page — everything else (orders/buyouts/ad_budget/profit, stock,
    КРПП, margins) is computed the same way, just summed across products
    instead of one."""

    product_id: str | None = None
    product_name: str = "Итого"
    product_sku: str | None = None
    product_offer_id: str | None = None
    product_image_url: str | None = None

    orders: MetricPlanFactActual
    buyouts: MetricPlanFactActual
    ad_budget: MetricPlanFactActual
    profit: MetricPlanFactActual

    drr_pct_actual: float | None = None  # факт: расход на рекламу / выручка выкупа за месяц-к-текущему-дню

    stock_total_units: int | None = None
    stock_fbo_units: int | None = None
    stock_fbs_units: int | None = None
    days_of_stock_remaining: float | None = None  # None если нет остатка или ещё не было продаж в этом месяце

    cost_known: bool = False  # есть ли себестоимость — от этого зависят profit/КРПП/маржа
    krpp_pct: float | None = None  # Прибыль с ДРР / Прибыль до ДРР × 100 (см. README — формула подтверждена пользователем)
    margin_before_ad_pct: float | None = None  # Прибыль до ДРР / Выручка выкупа × 100
    margin_after_ad_pct: float | None = None  # Прибыль с ДРР / Выручка выкупа × 100

    localization_pct: float | None = None  # ВСЕГДА None — Ozon не подтвердил метод API для этого (см. README)

    daily: list[DailyBreakdownEntry] = []


class ProductPlannerOut(BaseModel):
    year: int
    month: int
    days_in_month: int
    elapsed_days: int
    has_data: bool  # False только если в магазине вообще нет товаров
    total: ProductPlannerRow | None = None
    rows: list[ProductPlannerRow] = []


class ProductMonthlyPlanIn(BaseModel):
    plan_orders_units: int | None = None
    plan_orders_sum_rub: float | None = None
    plan_ad_budget_rub: float | None = None


class BulkPlanEntry(ProductMonthlyPlanIn):
    product_id: str


class BulkPlanIn(BaseModel):
    """Body for the mass plan-entry screen — one PUT saves every row's plan
    at once instead of N separate per-product requests (see
    app.api.routes.product_planner.bulk_set_product_monthly_plans)."""

    entries: list[BulkPlanEntry] = []


class SuggestedPlan(BaseModel):
    """Not persisted — a preview the seller can accept (by saving it via the
    normal plan-editing endpoint) or ignore. based_on_months is 0 when the
    store simply doesn't have that much completed history yet for this
    product, in which case every suggested_* field is None rather than a
    guess from too little data."""

    product_id: str
    based_on_months: int
    suggested_orders_units: int | None = None
    suggested_orders_sum_rub: float | None = None
    suggested_ad_budget_rub: float | None = None
