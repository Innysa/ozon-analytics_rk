"""Aggregates product-card statistics into period totals plus a small set of
conversion rates this app computes itself from the summed counts.

These calculated rates are explicitly NOT an attempt to reproduce Ozon's own
per-row conversion percentages (drr_pct_ozon, conv_*_pct_ozon on
ProductCardStatistic) — a real sample showed Ozon's own
"Конверсия из корзины в заказ" does not equal ordered_units/cart_adds_total
for that row, meaning its exact methodology involves something beyond the
plain counts it reports alongside it. Rather than guess at that formula, this
module computes its own straightforward, documented ratios over the summed
counts and never claims they match Ozon's number.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product_card_statistic import ProductCardStatistic
from app.schemas.product_analytics import ProductCardAnalyticsOut


def compute_product_card_analytics(
    db: Session,
    *,
    store_id: str,
    product_id: str | None = None,
    date_from=None,
    date_to=None,
) -> ProductCardAnalyticsOut:
    stmt = select(ProductCardStatistic).where(ProductCardStatistic.store_id == store_id)
    if product_id:
        stmt = stmt.where(ProductCardStatistic.product_id == product_id)
    if date_from:
        stmt = stmt.where(ProductCardStatistic.date >= date_from)
    if date_to:
        stmt = stmt.where(ProductCardStatistic.date <= date_to)

    rows = db.scalars(stmt.order_by(ProductCardStatistic.date)).all()
    if not rows:
        return ProductCardAnalyticsOut(has_data=False)

    def total(attr: str) -> int:
        return sum(getattr(r, attr) or 0 for r in rows)

    total_impressions = total("impressions_total")
    total_card_visits = total("card_visits")
    total_cart_adds = total("cart_adds_total")
    total_ordered_units = total("ordered_units")
    total_delivered_units = total("delivered_units")
    total_bought_out_units = total("bought_out_units")
    total_cancelled_units = total("cancelled_units_by_order_date")
    total_returned_units = total("returned_units_by_order_date")
    total_ordered_sum_rub = sum(float(r.ordered_sum_actual_price_rub or 0) for r in rows)

    latest = rows[-1]

    return ProductCardAnalyticsOut(
        has_data=True,
        date_from=rows[0].date,
        date_to=rows[-1].date,
        total_impressions=total_impressions,
        total_card_visits=total_card_visits,
        total_cart_adds=total_cart_adds,
        total_ordered_units=total_ordered_units,
        total_delivered_units=total_delivered_units,
        total_bought_out_units=total_bought_out_units,
        total_cancelled_units=total_cancelled_units,
        total_returned_units=total_returned_units,
        total_ordered_sum_rub=round(total_ordered_sum_rub, 2),
        latest_stock=latest.stock_end_of_period,
        latest_reviews_count=latest.reviews_count,
        latest_rating=float(latest.rating) if latest.rating is not None else None,
        latest_avg_price_rub=float(latest.avg_price_rub) if latest.avg_price_rub is not None else None,
        latest_price_index_label=latest.price_index_label_ozon,
        cart_conversion_calculated_pct=round(total_cart_adds / total_impressions * 100, 2) if total_impressions else None,
        order_conversion_calculated_pct=round(total_ordered_units / total_cart_adds * 100, 2) if total_cart_adds else None,
        buyout_rate_calculated_pct=round(total_bought_out_units / total_ordered_units * 100, 2) if total_ordered_units else None,
        rating_trend=[{"date": r.date.isoformat(), "rating": float(r.rating)} for r in rows if r.rating is not None],
        stock_trend=[{"date": r.date.isoformat(), "stock": r.stock_end_of_period} for r in rows if r.stock_end_of_period is not None],
    )
