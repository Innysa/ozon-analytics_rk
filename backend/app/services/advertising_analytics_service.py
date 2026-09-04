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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.advertising_campaign import AdvertisingCampaign
from app.models.advertising_statistic import AdvertisingStatistic
from app.models.product import Product
from app.schemas.advertising import (
    AdvertisingAnalyticsOut,
    CampaignBreakdown,
    ProductBreakdown,
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
