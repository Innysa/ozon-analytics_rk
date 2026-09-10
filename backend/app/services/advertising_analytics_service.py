"""ДРР (доля рекламных расходов) and ROAS, computed by this app from imported
advertising statistics — always kept labeled separately from the per-row ДРР
values Ozon itself reports in the export (drr_promo_pct_ozon/drr_total_pct_ozon
on AdvertisingStatistic), because those per-row percentages cannot be validly
averaged across rows; only summing spend and sales first, then dividing, is
mathematically correct for an aggregate.

  ДРР (%) = расход / продажи * 100   — доля рекламных расходов в выручке
  ROAS    = продажи / расход          — возврат на рекламные расходы (х раз)

Both are undefined (None, never 0) when there is no spend or no sales to
divide by — never fabricated.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.advertising_campaign import AdvertisingCampaign
from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
from app.models.advertising_statistic import AdvertisingStatistic
from app.models.product import Product
from app.schemas.advertising import (
    AdvertisingAnalyticsOut,
    CampaignAutoDailyComparison,
    CampaignAutoDailyDetailOut,
    CampaignBreakdown,
    CampaignDailyComparison,
    CampaignDetailOut,
    MetricComparison,
    ProductAdCampaignBreakdown,
    ProductAdvertisingAutoDailyOut,
    ProductBreakdown,
    ProductCampaignDailyRow,
)


def _drr(spend: float, sales: float) -> float | None:
    if not sales:
        return None
    return round(spend / sales * 100, 2)


def _roas(spend: float, sales: float) -> float | None:
    if not spend:
        return None
    return round(sales / spend, 3)


def compute_advertising_analytics(
    db: Session,
    *,
    store_id: str,
    product_id: str | None = None,
    date_from=None,
    date_to=None,
) -> AdvertisingAnalyticsOut:
    stmt = select(AdvertisingStatistic).where(AdvertisingStatistic.store_id == store_id)
    if product_id:
        stmt = stmt.where(AdvertisingStatistic.product_id == product_id)
    if date_from:
        stmt = stmt.where(AdvertisingStatistic.period_end >= date_from)
    if date_to:
        stmt = stmt.where(AdvertisingStatistic.period_start <= date_to)

    rows = db.scalars(stmt).all()
    if not rows:
        return AdvertisingAnalyticsOut(has_data=False)

    total_spend = sum(float(r.spend_rub) for r in rows)
    total_sales = sum(float(r.sales_promo_rub or 0) for r in rows)
    total_impressions = sum(r.impressions or 0 for r in rows)
    total_clicks = sum(r.clicks or 0 for r in rows)
    total_units_sold = sum(r.units_sold or 0 for r in rows)

    by_campaign: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        agg = by_campaign[r.campaign_id or r.ozon_campaign_id]
        agg[0] += float(r.spend_rub)
        agg[1] += float(r.sales_promo_rub or 0)

    campaign_names = {
        c.id: (c.name or c.ozon_campaign_id)
        for c in db.scalars(
            select(AdvertisingCampaign).where(AdvertisingCampaign.store_id == store_id)
        ).all()
    }

    campaign_breakdown = [
        CampaignBreakdown(
            campaign_id=cid,
            campaign_name=campaign_names.get(cid, cid),
            spend_rub=round(spend, 2),
            sales_promo_rub=round(sales, 2),
            drr_calculated_pct=_drr(spend, sales),
            roas_calculated=_roas(spend, sales),
        )
        for cid, (spend, sales) in sorted(by_campaign.items(), key=lambda kv: kv[1][0], reverse=True)
    ]

    by_product: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        agg = by_product[r.product_id or r.ozon_sku]
        agg[0] += float(r.spend_rub)
        agg[1] += float(r.sales_promo_rub or 0)

    product_names = {
        p.id: p.name
        for p in db.scalars(select(Product).where(Product.store_id == store_id)).all()
    }

    product_breakdown = [
        ProductBreakdown(
            product_id=pid,
            product_name=product_names.get(pid, pid),
            spend_rub=round(spend, 2),
            sales_promo_rub=round(sales, 2),
            drr_calculated_pct=_drr(spend, sales),
            roas_calculated=_roas(spend, sales),
        )
        for pid, (spend, sales) in sorted(by_product.items(), key=lambda kv: kv[1][0], reverse=True)
    ]

    return AdvertisingAnalyticsOut(
        has_data=True,
        period_start=min(r.period_start for r in rows),
        period_end=max(r.period_end for r in rows),
        total_spend_rub=round(total_spend, 2),
        total_sales_promo_rub=round(total_sales, 2),
        total_impressions=total_impressions,
        total_clicks=total_clicks,
        total_units_sold=total_units_sold,
        drr_calculated_pct=_drr(total_spend, total_sales),
        roas_calculated=_roas(total_spend, total_sales),
        ctr_calculated_pct=round(total_clicks / total_impressions * 100, 2) if total_impressions else None,
        avg_cpc_calculated_rub=round(total_spend / total_clicks, 2) if total_clicks else None,
        by_campaign=campaign_breakdown[:20],
        by_product=product_breakdown[:20],
    )


def _compare(today: float, yesterday: float) -> MetricComparison:
    delta = today - yesterday
    direction = "up" if delta > 0 else "down" if delta < 0 else None
    delta_pct = round(delta / yesterday * 100, 2) if yesterday else None
    return MetricComparison(
        today=round(today, 2), yesterday=round(yesterday, 2), delta=round(delta, 2), delta_pct=delta_pct, direction=direction
    )


def compute_campaign_auto_daily_detail(db: Session, *, store_id: str, campaign_id: str) -> CampaignAutoDailyDetailOut:
    """Same idea as compute_campaign_detail below, but aggregated from the
    automatically-collected AdvertisingDailyStatistic rows (Ozon Performance
    API async statistics-report sync) instead of the CSV-uploaded
    AdvertisingStatistic. Deliberately a separate function/return type, never
    merged into compute_campaign_detail's totals — see
    AdvertisingDailyStatistic's own docstring for why summing the two sources
    would risk double-counting spend/revenue. Every row here is already
    exactly one day, so (unlike the CSV path) a comparison only needs two
    distinct dates, not a period_start == period_end check."""
    rows = db.scalars(
        select(AdvertisingDailyStatistic).where(
            AdvertisingDailyStatistic.store_id == store_id,
            AdvertisingDailyStatistic.campaign_id == campaign_id,
        )
    ).all()
    if not rows:
        return CampaignAutoDailyDetailOut(has_data=False)

    total_spend = sum(float(r.spend_rub or 0) for r in rows)
    total_revenue = sum(float(r.revenue_rub or 0) for r in rows)
    total_impressions = sum(r.impressions or 0 for r in rows)
    total_clicks = sum(r.clicks or 0 for r in rows)
    total_orders = sum(r.orders or 0 for r in rows)

    by_date: dict[date, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "revenue": 0.0, "impressions": 0.0, "clicks": 0.0})
    for r in rows:
        agg = by_date[r.date]
        agg["spend"] += float(r.spend_rub or 0)
        agg["revenue"] += float(r.revenue_rub or 0)
        agg["impressions"] += r.impressions or 0
        agg["clicks"] += r.clicks or 0

    comparison = None
    reason = None
    dates_sorted = sorted(by_date.keys(), reverse=True)
    if len(dates_sorted) >= 2:
        today_d, yesterday_d = dates_sorted[0], dates_sorted[1]
        today_vals, yesterday_vals = by_date[today_d], by_date[yesterday_d]
        comparison = CampaignAutoDailyComparison(
            date_today=today_d,
            date_yesterday=yesterday_d,
            spend_rub=_compare(today_vals["spend"], yesterday_vals["spend"]),
            impressions=_compare(today_vals["impressions"], yesterday_vals["impressions"]),
            clicks=_compare(today_vals["clicks"], yesterday_vals["clicks"]),
            revenue_rub=_compare(today_vals["revenue"], yesterday_vals["revenue"]),
        )
    else:
        reason = (
            "Сравнение с предыдущим днём недоступно — нужно как минимум два разных дня "
            f"автоматически собранной статистики для этой кампании; сейчас доступно: {len(dates_sorted)}."
        )

    return CampaignAutoDailyDetailOut(
        has_data=True,
        total_spend_rub=round(total_spend, 2),
        total_revenue_rub=round(total_revenue, 2),
        total_impressions=total_impressions,
        total_clicks=total_clicks,
        total_orders=total_orders,
        drr_calculated_pct=_drr(total_spend, total_revenue),
        roas_calculated=_roas(total_spend, total_revenue),
        period_start=min(by_date.keys()),
        period_end=max(by_date.keys()),
        daily_comparison=comparison,
        daily_comparison_unavailable_reason=reason,
    )


def compute_product_advertising_auto_daily(db: Session, *, store_id: str, ozon_sku: str) -> ProductAdvertisingAutoDailyOut:
    """Same aggregation as compute_campaign_auto_daily_detail, but sliced by
    SKU across every campaign that advertised it, instead of by one campaign
    across every SKU it covers — a campaign usually spans many products, so
    this groups by ozon_campaign_id to show which campaigns actually drive
    this product's numbers."""
    rows = db.scalars(
        select(AdvertisingDailyStatistic).where(
            AdvertisingDailyStatistic.store_id == store_id,
            AdvertisingDailyStatistic.ozon_sku == ozon_sku,
        )
    ).all()
    if not rows:
        return ProductAdvertisingAutoDailyOut(has_data=False)

    total_spend = sum(float(r.spend_rub or 0) for r in rows)
    total_revenue = sum(float(r.revenue_rub or 0) for r in rows)
    total_impressions = sum(r.impressions or 0 for r in rows)
    total_clicks = sum(r.clicks or 0 for r in rows)
    total_orders = sum(r.orders or 0 for r in rows)

    by_date: dict[date, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "revenue": 0.0, "impressions": 0.0, "clicks": 0.0})
    for r in rows:
        agg = by_date[r.date]
        agg["spend"] += float(r.spend_rub or 0)
        agg["revenue"] += float(r.revenue_rub or 0)
        agg["impressions"] += r.impressions or 0
        agg["clicks"] += r.clicks or 0

    comparison = None
    reason = None
    dates_sorted = sorted(by_date.keys(), reverse=True)
    if len(dates_sorted) >= 2:
        today_d, yesterday_d = dates_sorted[0], dates_sorted[1]
        today_vals, yesterday_vals = by_date[today_d], by_date[yesterday_d]
        comparison = CampaignAutoDailyComparison(
            date_today=today_d,
            date_yesterday=yesterday_d,
            spend_rub=_compare(today_vals["spend"], yesterday_vals["spend"]),
            impressions=_compare(today_vals["impressions"], yesterday_vals["impressions"]),
            clicks=_compare(today_vals["clicks"], yesterday_vals["clicks"]),
            revenue_rub=_compare(today_vals["revenue"], yesterday_vals["revenue"]),
        )
    else:
        reason = (
            "Сравнение с предыдущим днём недоступно — нужно как минимум два разных дня "
            f"автоматически собранной статистики для этого товара; сейчас доступно: {len(dates_sorted)}."
        )

    by_campaign_agg: dict[str, dict[str, float]] = defaultdict(
        lambda: {"spend": 0.0, "revenue": 0.0, "impressions": 0.0, "clicks": 0.0, "orders": 0.0}
    )
    for r in rows:
        agg = by_campaign_agg[r.ozon_campaign_id]
        agg["spend"] += float(r.spend_rub or 0)
        agg["revenue"] += float(r.revenue_rub or 0)
        agg["impressions"] += r.impressions or 0
        agg["clicks"] += r.clicks or 0
        agg["orders"] += r.orders or 0

    campaigns_by_ozon_id = {
        c.ozon_campaign_id: c
        for c in db.scalars(select(AdvertisingCampaign).where(AdvertisingCampaign.store_id == store_id)).all()
    }

    by_campaign = [
        ProductAdCampaignBreakdown(
            campaign_id=ozon_campaign_id,
            campaign_name=(campaigns_by_ozon_id[ozon_campaign_id].name or ozon_campaign_id)
            if ozon_campaign_id in campaigns_by_ozon_id
            else ozon_campaign_id,
            campaign_state=campaigns_by_ozon_id[ozon_campaign_id].state if ozon_campaign_id in campaigns_by_ozon_id else None,
            spend_rub=round(agg["spend"], 2),
            impressions=int(agg["impressions"]),
            clicks=int(agg["clicks"]),
            orders=int(agg["orders"]),
            revenue_rub=round(agg["revenue"], 2),
            drr_calculated_pct=_drr(agg["spend"], agg["revenue"]),
            roas_calculated=_roas(agg["spend"], agg["revenue"]),
        )
        for ozon_campaign_id, agg in sorted(by_campaign_agg.items(), key=lambda kv: kv[1]["spend"], reverse=True)
    ]

    return ProductAdvertisingAutoDailyOut(
        has_data=True,
        total_spend_rub=round(total_spend, 2),
        total_revenue_rub=round(total_revenue, 2),
        total_impressions=total_impressions,
        total_clicks=total_clicks,
        total_orders=total_orders,
        drr_calculated_pct=_drr(total_spend, total_revenue),
        roas_calculated=_roas(total_spend, total_revenue),
        period_start=min(by_date.keys()),
        period_end=max(by_date.keys()),
        daily_comparison=comparison,
        daily_comparison_unavailable_reason=reason,
        by_campaign=by_campaign,
    )


def compute_product_campaign_daily_rows(
    db: Session, *, store_id: str, ozon_sku: str, ozon_campaign_id: str
) -> list[ProductCampaignDailyRow]:
    """The expanded-row detail for one campaign on the product detail page's
    "Реклама" tab: every day of auto-collected AdvertisingDailyStatistic for
    this exact (sku, campaign) pair — the same source as
    compute_product_advertising_auto_daily's by_campaign totals, just not
    summed across dates this time. Filtered by ozon_campaign_id (not the
    internal campaign_id FK) so it still works for a campaign whose
    AdvertisingCampaign metadata was never synced."""
    rows = db.scalars(
        select(AdvertisingDailyStatistic).where(
            AdvertisingDailyStatistic.store_id == store_id,
            AdvertisingDailyStatistic.ozon_sku == ozon_sku,
            AdvertisingDailyStatistic.ozon_campaign_id == ozon_campaign_id,
        )
    ).all()

    by_date: dict[date, dict[str, float]] = defaultdict(
        lambda: {"spend": 0.0, "revenue": 0.0, "impressions": 0.0, "clicks": 0.0, "orders": 0.0}
    )
    for r in rows:
        agg = by_date[r.date]
        agg["spend"] += float(r.spend_rub or 0)
        agg["revenue"] += float(r.revenue_rub or 0)
        agg["impressions"] += r.impressions or 0
        agg["clicks"] += r.clicks or 0
        agg["orders"] += r.orders or 0

    return [
        ProductCampaignDailyRow(
            date=day,
            spend_rub=round(agg["spend"], 2),
            impressions=int(agg["impressions"]),
            clicks=int(agg["clicks"]),
            orders=int(agg["orders"]),
            revenue_rub=round(agg["revenue"], 2),
            drr_calculated_pct=_drr(agg["spend"], agg["revenue"]),
            roas_calculated=_roas(agg["spend"], agg["revenue"]),
        )
        for day, agg in sorted(by_date.items(), reverse=True)
    ]


def compute_campaign_detail(db: Session, *, store_id: str, campaign_id: str) -> CampaignDetailOut:
    """Aggregates every uploaded advertising_statistics row for one campaign
    (spend/impressions/clicks/sales, summed across all its SKU rows and
    whatever periods have been uploaded), plus a day-over-day comparison —
    only when at least two distinct dates with period_start == period_end
    exist for this campaign. A weekly/monthly report alone can never produce
    a same-length "day" to compare against, so this never fabricates one.

    Always also attaches auto_daily — the same aggregation over the separate,
    automatically-collected AdvertisingDailyStatistic source — regardless of
    whether this (CSV) source has any data, so the frontend can show
    auto-collected numbers for a campaign even when nothing was ever
    uploaded manually for it."""
    auto_daily = compute_campaign_auto_daily_detail(db, store_id=store_id, campaign_id=campaign_id)

    rows = db.scalars(
        select(AdvertisingStatistic).where(
            AdvertisingStatistic.store_id == store_id,
            AdvertisingStatistic.campaign_id == campaign_id,
        )
    ).all()
    if not rows:
        return CampaignDetailOut(campaign_id=campaign_id, has_data=False, auto_daily=auto_daily)

    total_spend = sum(float(r.spend_rub) for r in rows)
    total_sales = sum(float(r.sales_promo_rub or 0) for r in rows)
    total_impressions = sum(r.impressions or 0 for r in rows)
    total_clicks = sum(r.clicks or 0 for r in rows)
    total_units = sum(r.units_sold or 0 for r in rows)

    by_date: dict[date, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "sales": 0.0, "impressions": 0.0, "clicks": 0.0})
    for r in rows:
        if r.period_start != r.period_end:
            continue  # not a genuine single-day report — can't anchor it to one date
        agg = by_date[r.period_start]
        agg["spend"] += float(r.spend_rub)
        agg["sales"] += float(r.sales_promo_rub or 0)
        agg["impressions"] += r.impressions or 0
        agg["clicks"] += r.clicks or 0

    comparison = None
    reason = None
    dates_sorted = sorted(by_date.keys(), reverse=True)
    if len(dates_sorted) >= 2:
        today_d, yesterday_d = dates_sorted[0], dates_sorted[1]
        today_vals, yesterday_vals = by_date[today_d], by_date[yesterday_d]
        comparison = CampaignDailyComparison(
            date_today=today_d,
            date_yesterday=yesterday_d,
            spend_rub=_compare(today_vals["spend"], yesterday_vals["spend"]),
            impressions=_compare(today_vals["impressions"], yesterday_vals["impressions"]),
            clicks=_compare(today_vals["clicks"], yesterday_vals["clicks"]),
            sales_promo_rub=_compare(today_vals["sales"], yesterday_vals["sales"]),
        )
    else:
        reason = (
            "Сравнение с предыдущим днём недоступно — нужно как минимум два отдельных "
            "дневных отчёта (период = один день) для этой кампании; загружено с суточной "
            f"детализацией: {len(dates_sorted)}."
        )

    return CampaignDetailOut(
        campaign_id=campaign_id,
        has_data=True,
        total_spend_rub=round(total_spend, 2),
        total_sales_promo_rub=round(total_sales, 2),
        total_impressions=total_impressions,
        total_clicks=total_clicks,
        total_units_sold=total_units,
        drr_calculated_pct=_drr(total_spend, total_sales),
        roas_calculated=_roas(total_spend, total_sales),
        period_start=min(r.period_start for r in rows),
        period_end=max(r.period_end for r in rows),
        daily_comparison=comparison,
        daily_comparison_unavailable_reason=reason,
        auto_daily=auto_daily,
    )
