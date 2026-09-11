"""«РНП Товары» — per-product monthly planner. Computes, per product, four
plan/forecast/factual metric groups (Заказы, Выкупы, Рекламный бюджет,
Прибыль) plus a handful of derived indicators (КРПП, маржа до/с ДРР,
остатки, «хватит на»), from data ALREADY collected automatically by other
sync services — this module reads, it never calls Ozon itself:

  - Заказы/Выкупы: ProductOrderDailyStatistic (same Ozon Seller API
    postings sync as the "РНП" day-based page, sliced by SKU)
  - Рекламный бюджет: AdvertisingDailyStatistic.spend_rub, summed by SKU
    across every campaign that advertised it that day
  - Остатки: Product.fbo_stock/fbs_stock (catalog sync snapshot, not
    historical)
  - Прибыль/КРПП/маржа: computed here from the above plus
    Product.cost_price_rub (manually entered — no Ozon API exposes it)

The only thing NOT computed here is «Локализация» (% локальных заказов):
confirmed 2026-09-11 that Ozon's own support answer only points to a
seller-cabinet UI report ("Локальность продаж"), not a Seller API method —
nothing in this account's own full method list matches it either. Every
ProductPlannerRow.localization_pct is therefore always None until a real
API contract is confirmed (see README's "Заказы и финансы" section).

КРПП (Коэффициент рентабельности рекламных расходов) — formula CONFIRMED
by the user directly (2026-09-11, from their own reference spreadsheet's
tooltip): "какая доля прибыли остаётся после вычета рекламных расходов
относительно общей прибыли" = Прибыль_с_ДРР / Прибыль_до_ДРР. Маржа до/с
ДРР are the same two profit figures as a % of Выкуплено (buyout revenue),
which is the natural companion figure the КРПП tooltip doesn't itself
define but is the standard "margin %" reading of "Прибыль".

Прогноз (forecast): CONFIRMED with the user — a simple linear
extrapolation for now (факт_к_текущему_дню / прошедшие_дни × дней_в_месяце),
not seasonality-adjusted; can be revisited later if it proves too rough.
Only meaningful for the CURRENT calendar month (a fully past month has
elapsed_days == days_in_month, so forecast == actual; a fully future month
has elapsed_days == 0, so forecast is None — nothing to extrapolate from).

«Хватит на» (days_of_stock_remaining): CONFIRMED with the user to be based
on the CURRENT calendar month's pace (same elapsed-days window as the
forecast above, not a rolling 7/14/30-day window) — stock / (факт_буyouts_
so_far / elapsed_days). None when there's no stock, or no buyout units yet
this month to derive a pace from.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
from app.models.product import Product
from app.models.product_monthly_plan import ProductMonthlyPlan
from app.models.product_order_daily_statistic import ProductOrderDailyStatistic
from app.schemas.product_planner import (
    DailyBreakdownEntry,
    MetricPlanFactActual,
    ProductPlannerOut,
    ProductPlannerRow,
    SuggestedPlan,
)

HISTORY_MONTHS_FOR_SUGGESTION = 3


@dataclass
class _DailyAgg:
    orders_units: int = 0
    orders_sum_rub: float = 0.0
    buyouts_units: int = 0
    buyouts_sum_rub: float = 0.0
    ad_spend_rub: float = 0.0


@dataclass
class _ProductAgg:
    orders_units: int = 0
    orders_sum_rub: float = 0.0
    buyouts_units: int = 0
    buyouts_sum_rub: float = 0.0
    ad_spend_rub: float = 0.0
    by_date: dict[date, _DailyAgg] = field(default_factory=dict)

    def day(self, d: date) -> _DailyAgg:
        return self.by_date.setdefault(d, _DailyAgg())


def _month_bounds(year: int, month: int) -> tuple[date, date, int]:
    days_in_month = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, days_in_month), days_in_month


def _elapsed_days(year: int, month: int, days_in_month: int) -> int:
    today = datetime.now(timezone.utc).date()
    if (year, month) < (today.year, today.month):
        return days_in_month  # a fully past month — forecast == actual
    if (year, month) > (today.year, today.month):
        return 0  # a fully future month — nothing to extrapolate from
    return today.day


def _forecast(actual: float | None, elapsed_days: int, days_in_month: int) -> float | None:
    if actual is None or elapsed_days <= 0:
        return None
    return round(actual / elapsed_days * days_in_month, 2)


def _metric(
    *, plan_month: float | None, actual_month: float | None, elapsed_days: int, days_in_month: int,
    plan_month_units: int | None = None, actual_month_units: int | None = None,
) -> MetricPlanFactActual:
    forecast_units = _forecast(float(actual_month_units), elapsed_days, days_in_month) if actual_month_units is not None else None
    return MetricPlanFactActual(
        plan_day_rub=round(plan_month / days_in_month, 2) if plan_month is not None else None,
        plan_month_rub=plan_month,
        forecast_month_rub=_forecast(actual_month, elapsed_days, days_in_month),
        actual_month_rub=actual_month,
        plan_day_units=round(plan_month_units / days_in_month, 2) if plan_month_units is not None else None,
        plan_month_units=plan_month_units,
        forecast_month_units=round(forecast_units) if forecast_units is not None else None,
        actual_month_units=actual_month_units,
    )


def _aggregate_month(db: Session, *, store_id: str, date_from: date, date_to: date) -> dict[str, _ProductAgg]:
    by_sku: dict[str, _ProductAgg] = {}

    order_rows = db.scalars(
        select(ProductOrderDailyStatistic).where(
            ProductOrderDailyStatistic.store_id == store_id,
            ProductOrderDailyStatistic.date >= date_from,
            ProductOrderDailyStatistic.date <= date_to,
        )
    ).all()
    for r in order_rows:
        agg = by_sku.setdefault(r.ozon_sku, _ProductAgg())
        agg.orders_units += r.ordered_units
        agg.orders_sum_rub += float(r.ordered_sum_rub or 0)
        agg.buyouts_units += r.delivered_units
        agg.buyouts_sum_rub += float(r.delivered_sum_rub or 0)
        day = agg.day(r.date)
        day.orders_units += r.ordered_units
        day.orders_sum_rub += float(r.ordered_sum_rub or 0)
        day.buyouts_units += r.delivered_units
        day.buyouts_sum_rub += float(r.delivered_sum_rub or 0)

    ad_rows = db.execute(
        select(AdvertisingDailyStatistic.ozon_sku, AdvertisingDailyStatistic.date, AdvertisingDailyStatistic.spend_rub).where(
            AdvertisingDailyStatistic.store_id == store_id,
            AdvertisingDailyStatistic.date >= date_from,
            AdvertisingDailyStatistic.date <= date_to,
        )
    ).all()
    for sku, d, spend_rub in ad_rows:
        agg = by_sku.setdefault(sku, _ProductAgg())
        spend = float(spend_rub or 0)
        agg.ad_spend_rub += spend
        agg.day(d).ad_spend_rub += spend

    return by_sku


def _row_for_product(
    *, product: Product | None, agg: _ProductAgg, plan: ProductMonthlyPlan | None,
    elapsed_days: int, days_in_month: int,
) -> ProductPlannerRow:
    profit_before_ad = None
    profit_after_ad = None
    cost_known = product is not None and product.cost_price_rub is not None
    if cost_known:
        cost_total = float(product.cost_price_rub) * agg.buyouts_units
        profit_before_ad = agg.buyouts_sum_rub - cost_total
        profit_after_ad = profit_before_ad - agg.ad_spend_rub

    krpp_pct = (
        round(profit_after_ad / profit_before_ad * 100, 2)
        if profit_before_ad not in (None, 0) and profit_after_ad is not None
        else None
    )
    margin_before_pct = (
        round(profit_before_ad / agg.buyouts_sum_rub * 100, 2) if profit_before_ad is not None and agg.buyouts_sum_rub > 0 else None
    )
    margin_after_pct = (
        round(profit_after_ad / agg.buyouts_sum_rub * 100, 2) if profit_after_ad is not None and agg.buyouts_sum_rub > 0 else None
    )
    drr_pct_actual = round(agg.ad_spend_rub / agg.buyouts_sum_rub * 100, 2) if agg.buyouts_sum_rub > 0 else None

    stock_fbo = product.fbo_stock if product and product.fbo_stock is not None else None
    stock_fbs = product.fbs_stock if product and product.fbs_stock is not None else None
    stock_total = (stock_fbo or 0) + (stock_fbs or 0) if (stock_fbo is not None or stock_fbs is not None) else None
    daily_pace = agg.buyouts_units / elapsed_days if elapsed_days > 0 and agg.buyouts_units > 0 else None
    days_of_stock_remaining = round(stock_total / daily_pace, 1) if stock_total is not None and daily_pace else None

    daily = [
        DailyBreakdownEntry(
            date=d,
            orders_sum_rub=round(day.orders_sum_rub, 2),
            orders_units=day.orders_units,
            buyouts_sum_rub=round(day.buyouts_sum_rub, 2),
            buyouts_units=day.buyouts_units,
            ad_spend_rub=round(day.ad_spend_rub, 2),
            profit_rub=(
                round(day.buyouts_sum_rub - float(product.cost_price_rub) * day.buyouts_units - day.ad_spend_rub, 2)
                if cost_known and product is not None
                else None
            ),
        )
        for d, day in sorted(agg.by_date.items())
    ]

    return ProductPlannerRow(
        product_id=product.id if product else None,
        product_name=product.name if product else "Итого",
        product_sku=product.ozon_sku if product else None,
        product_offer_id=product.offer_id if product else None,
        product_image_url=product.image_url if product else None,
        orders=_metric(
            plan_month=float(plan.plan_orders_sum_rub) if plan and plan.plan_orders_sum_rub is not None else None,
            actual_month=round(agg.orders_sum_rub, 2),
            elapsed_days=elapsed_days, days_in_month=days_in_month,
            plan_month_units=plan.plan_orders_units if plan else None,
            actual_month_units=agg.orders_units,
        ),
        buyouts=_metric(
            plan_month=float(plan.plan_buyouts_sum_rub) if plan and plan.plan_buyouts_sum_rub is not None else None,
            actual_month=round(agg.buyouts_sum_rub, 2),
            elapsed_days=elapsed_days, days_in_month=days_in_month,
            plan_month_units=plan.plan_buyouts_units if plan else None,
            actual_month_units=agg.buyouts_units,
        ),
        ad_budget=_metric(
            plan_month=float(plan.plan_ad_budget_rub) if plan and plan.plan_ad_budget_rub is not None else None,
            actual_month=round(agg.ad_spend_rub, 2),
            elapsed_days=elapsed_days, days_in_month=days_in_month,
        ),
        profit=_metric(
            plan_month=float(plan.plan_profit_rub) if plan and plan.plan_profit_rub is not None else None,
            actual_month=round(profit_after_ad, 2) if profit_after_ad is not None else None,
            elapsed_days=elapsed_days, days_in_month=days_in_month,
        ),
        drr_pct_actual=drr_pct_actual,
        stock_total_units=stock_total,
        stock_fbo_units=stock_fbo,
        stock_fbs_units=stock_fbs,
        days_of_stock_remaining=days_of_stock_remaining,
        cost_known=cost_known,
        krpp_pct=krpp_pct,
        margin_before_ad_pct=margin_before_pct,
        margin_after_ad_pct=margin_after_pct,
        localization_pct=None,
        daily=daily,
    )


def compute_product_planner(db: Session, *, store_id: str, year: int, month: int) -> ProductPlannerOut:
    date_from, date_to, days_in_month = _month_bounds(year, month)
    elapsed_days = _elapsed_days(year, month, days_in_month)

    products = db.scalars(
        select(Product).where(Product.store_id == store_id, Product.is_archived.is_(False)).order_by(Product.name)
    ).all()
    if not products:
        return ProductPlannerOut(year=year, month=month, days_in_month=days_in_month, elapsed_days=elapsed_days, has_data=False)

    agg_by_sku = _aggregate_month(db, store_id=store_id, date_from=date_from, date_to=date_to)
    plans = {
        p.product_id: p
        for p in db.scalars(
            select(ProductMonthlyPlan).where(
                ProductMonthlyPlan.store_id == store_id, ProductMonthlyPlan.year == year, ProductMonthlyPlan.month == month,
            )
        )
    }

    rows = []
    total_agg = _ProductAgg()
    total_plan_orders_units = total_plan_orders_sum = total_plan_buyouts_units = total_plan_buyouts_sum = 0.0
    total_plan_ad_budget = total_plan_profit = 0.0
    any_plan = False
    total_stock = 0
    any_stock = False
    total_cost_known_buyouts_sum = total_cost_known_profit_before = total_cost_known_profit_after = 0.0
    any_cost_known = False

    for product in products:
        agg = agg_by_sku.get(product.ozon_sku, _ProductAgg())
        plan = plans.get(product.id)
        row = _row_for_product(product=product, agg=agg, plan=plan, elapsed_days=elapsed_days, days_in_month=days_in_month)
        rows.append(row)

        total_agg.orders_units += agg.orders_units
        total_agg.orders_sum_rub += agg.orders_sum_rub
        total_agg.buyouts_units += agg.buyouts_units
        total_agg.buyouts_sum_rub += agg.buyouts_sum_rub
        total_agg.ad_spend_rub += agg.ad_spend_rub
        for d, day in agg.by_date.items():
            total_day = total_agg.day(d)
            total_day.orders_units += day.orders_units
            total_day.orders_sum_rub += day.orders_sum_rub
            total_day.buyouts_units += day.buyouts_units
            total_day.buyouts_sum_rub += day.buyouts_sum_rub
            total_day.ad_spend_rub += day.ad_spend_rub

        if plan:
            any_plan = True
            total_plan_orders_units += plan.plan_orders_units or 0
            total_plan_orders_sum += float(plan.plan_orders_sum_rub or 0)
            total_plan_buyouts_units += plan.plan_buyouts_units or 0
            total_plan_buyouts_sum += float(plan.plan_buyouts_sum_rub or 0)
            total_plan_ad_budget += float(plan.plan_ad_budget_rub or 0)
            total_plan_profit += float(plan.plan_profit_rub or 0)

        if row.stock_total_units is not None:
            any_stock = True
            total_stock += row.stock_total_units
        if row.cost_known:
            # Recomputed directly from the product's own cost price rather
            # than back-derived from row.margin_*_pct (which is already
            # rounded to 2 decimals) — avoids compounding rounding error
            # across many products in the "Итого" row.
            any_cost_known = True
            profit_before_ad = agg.buyouts_sum_rub - float(product.cost_price_rub) * agg.buyouts_units
            profit_after_ad = profit_before_ad - agg.ad_spend_rub
            total_cost_known_buyouts_sum += agg.buyouts_sum_rub
            total_cost_known_profit_before += profit_before_ad
            total_cost_known_profit_after += profit_after_ad

    total_pace = total_agg.buyouts_units / elapsed_days if elapsed_days > 0 and total_agg.buyouts_units > 0 else None
    total_row = ProductPlannerRow(
        product_name="Итого",
        orders=_metric(
            plan_month=total_plan_orders_sum if any_plan else None, actual_month=round(total_agg.orders_sum_rub, 2),
            elapsed_days=elapsed_days, days_in_month=days_in_month,
            plan_month_units=int(total_plan_orders_units) if any_plan else None, actual_month_units=total_agg.orders_units,
        ),
        buyouts=_metric(
            plan_month=total_plan_buyouts_sum if any_plan else None, actual_month=round(total_agg.buyouts_sum_rub, 2),
            elapsed_days=elapsed_days, days_in_month=days_in_month,
            plan_month_units=int(total_plan_buyouts_units) if any_plan else None, actual_month_units=total_agg.buyouts_units,
        ),
        ad_budget=_metric(
            plan_month=total_plan_ad_budget if any_plan else None, actual_month=round(total_agg.ad_spend_rub, 2),
            elapsed_days=elapsed_days, days_in_month=days_in_month,
        ),
        profit=_metric(
            plan_month=total_plan_profit if any_plan else None,
            actual_month=round(total_cost_known_profit_after, 2) if any_cost_known else None,
            elapsed_days=elapsed_days, days_in_month=days_in_month,
        ),
        drr_pct_actual=round(total_agg.ad_spend_rub / total_agg.buyouts_sum_rub * 100, 2) if total_agg.buyouts_sum_rub > 0 else None,
        stock_total_units=total_stock if any_stock else None,
        days_of_stock_remaining=round(total_stock / total_pace, 1) if any_stock and total_pace else None,
        cost_known=any_cost_known,
        krpp_pct=(
            round(total_cost_known_profit_after / total_cost_known_profit_before * 100, 2)
            if any_cost_known and total_cost_known_profit_before
            else None
        ),
        margin_before_ad_pct=(
            round(total_cost_known_profit_before / total_cost_known_buyouts_sum * 100, 2)
            if any_cost_known and total_cost_known_buyouts_sum > 0
            else None
        ),
        margin_after_ad_pct=(
            round(total_cost_known_profit_after / total_cost_known_buyouts_sum * 100, 2)
            if any_cost_known and total_cost_known_buyouts_sum > 0
            else None
        ),
        localization_pct=None,
        daily=[
            DailyBreakdownEntry(
                date=d, orders_sum_rub=round(day.orders_sum_rub, 2), orders_units=day.orders_units,
                buyouts_sum_rub=round(day.buyouts_sum_rub, 2), buyouts_units=day.buyouts_units,
                ad_spend_rub=round(day.ad_spend_rub, 2), profit_rub=None,
            )
            for d, day in sorted(total_agg.by_date.items())
        ],
    )

    return ProductPlannerOut(
        year=year, month=month, days_in_month=days_in_month, elapsed_days=elapsed_days,
        has_data=True, total=total_row, rows=rows,
    )


def suggest_plan(db: Session, *, store_id: str, product_id: str, year: int, month: int) -> SuggestedPlan:
    """Averages the product's factual figures over the last
    HISTORY_MONTHS_FOR_SUGGESTION calendar months strictly BEFORE
    (year, month) — a preview only, never written to ProductMonthlyPlan by
    this function itself (see this module's own docstring / the user's
    explicit request: "предложить план", not automatic)."""
    product = db.get(Product, product_id)
    if not product or product.store_id != store_id:
        return SuggestedPlan(product_id=product_id, based_on_months=0)

    months: list[tuple[int, int]] = []
    y, m = year, month
    for _ in range(HISTORY_MONTHS_FOR_SUGGESTION):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        months.append((y, m))

    orders_units = orders_sum = buyouts_units = buyouts_sum = ad_spend = 0.0
    profit_sum = 0.0
    months_with_data = 0
    for hy, hm in months:
        date_from, date_to, _ = _month_bounds(hy, hm)
        rows = db.scalars(
            select(ProductOrderDailyStatistic).where(
                ProductOrderDailyStatistic.store_id == store_id,
                ProductOrderDailyStatistic.ozon_sku == product.ozon_sku,
                ProductOrderDailyStatistic.date >= date_from,
                ProductOrderDailyStatistic.date <= date_to,
            )
        ).all()
        ad_rows = db.scalars(
            select(AdvertisingDailyStatistic.spend_rub).where(
                AdvertisingDailyStatistic.store_id == store_id,
                AdvertisingDailyStatistic.ozon_sku == product.ozon_sku,
                AdvertisingDailyStatistic.date >= date_from,
                AdvertisingDailyStatistic.date <= date_to,
            )
        ).all()
        if not rows and not ad_rows:
            continue
        months_with_data += 1
        month_orders_units = sum(r.ordered_units for r in rows)
        month_orders_sum = sum(float(r.ordered_sum_rub or 0) for r in rows)
        month_buyouts_units = sum(r.delivered_units for r in rows)
        month_buyouts_sum = sum(float(r.delivered_sum_rub or 0) for r in rows)
        month_ad_spend = sum(float(s or 0) for s in ad_rows)
        orders_units += month_orders_units
        orders_sum += month_orders_sum
        buyouts_units += month_buyouts_units
        buyouts_sum += month_buyouts_sum
        ad_spend += month_ad_spend
        if product.cost_price_rub is not None:
            profit_sum += month_buyouts_sum - float(product.cost_price_rub) * month_buyouts_units - month_ad_spend

    if months_with_data == 0:
        return SuggestedPlan(product_id=product_id, based_on_months=0)

    return SuggestedPlan(
        product_id=product_id,
        based_on_months=months_with_data,
        suggested_orders_units=round(orders_units / months_with_data),
        suggested_orders_sum_rub=round(orders_sum / months_with_data, 2),
        suggested_buyouts_units=round(buyouts_units / months_with_data),
        suggested_buyouts_sum_rub=round(buyouts_sum / months_with_data, 2),
        suggested_ad_budget_rub=round(ad_spend / months_with_data, 2),
        suggested_profit_rub=round(profit_sum / months_with_data, 2) if product.cost_price_rub is not None else None,
    )
