"""Aggregates the store-wide daily dashboard from three already-existing,
independent data sources — see app.schemas.dashboard's module docstring for
why each block is independently has_data and why spend_share_of_revenue_pct
is a distinct number from the per-campaign ДРР on the Реклама page.

Default period (when the caller doesn't pass one): the last
DASHBOARD_DEFAULT_LOOKBACK_DAYS days, compared against the immediately
preceding period of the same length — e.g. last 30 days vs. the 30 days
before that. A single arbitrary "yesterday vs. today" comparison would be
far noisier for a summary this coarse (orders/revenue here come from
whatever cadence the seller uploads "Аналитика → Товары" in, not
necessarily daily)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
from app.models.advertising_statistic import AdvertisingStatistic
from app.models.order_daily_statistic import OrderDailyStatistic
from app.models.product import Product
from app.models.product_card_statistic import ProductCardStatistic
from app.models.review import Review, ReviewStatus
from app.schemas.dashboard import (
    AdvertisingBlock,
    DashboardMetric,
    DashboardOut,
    InventoryBlock,
    MarginBlock,
    OrdersRevenueBlock,
    ReviewsBlock,
)


def _compare(current: float, previous: float | None) -> DashboardMetric:
    if previous is None:
        return DashboardMetric(current=round(current, 2), previous=None, delta_pct=None, direction=None)
    delta = current - previous
    delta_pct = round(delta / previous * 100, 2) if previous else None
    direction = "up" if delta > 0 else "down" if delta < 0 else None
    return DashboardMetric(current=round(current, 2), previous=round(previous, 2), delta_pct=delta_pct, direction=direction)


def _sum_product_card_stats(db: Session, *, store_id: str, date_from: date, date_to: date) -> tuple[int, float, int]:
    row = db.execute(
        select(
            func.coalesce(func.sum(ProductCardStatistic.ordered_units), 0),
            func.coalesce(func.sum(ProductCardStatistic.ordered_sum_actual_price_rub), 0),
            func.coalesce(func.sum(ProductCardStatistic.bought_out_units), 0),
        ).where(
            ProductCardStatistic.store_id == store_id,
            ProductCardStatistic.date >= date_from,
            ProductCardStatistic.date <= date_to,
        )
    ).one()
    return int(row[0]), float(row[1]), int(row[2])


def _has_any_product_card_stats(db: Session, *, store_id: str) -> bool:
    return db.scalar(select(ProductCardStatistic.id).where(ProductCardStatistic.store_id == store_id).limit(1)) is not None


def _sum_auto_ad_spend(db: Session, *, store_id: str, date_from: date, date_to: date) -> float:
    total = db.scalar(
        select(func.coalesce(func.sum(AdvertisingDailyStatistic.spend_rub), 0)).where(
            AdvertisingDailyStatistic.store_id == store_id,
            AdvertisingDailyStatistic.date >= date_from,
            AdvertisingDailyStatistic.date <= date_to,
        )
    )
    return float(total or 0)


def _has_any_auto_ad_stats(db: Session, *, store_id: str) -> bool:
    return (
        db.scalar(select(AdvertisingDailyStatistic.id).where(AdvertisingDailyStatistic.store_id == store_id).limit(1))
        is not None
    )


def _sum_manual_ad_spend(db: Session, *, store_id: str, date_from: date, date_to: date) -> float:
    # AdvertisingStatistic rows are period-level (period_start/period_end,
    # not a single day) — same convention as the Реклама page's own
    # aggregation: a row counts toward a range only if fully contained in
    # it, since a row straddling the boundary can't be split.
    total = db.scalar(
        select(func.coalesce(func.sum(AdvertisingStatistic.spend_rub), 0)).where(
            AdvertisingStatistic.store_id == store_id,
            AdvertisingStatistic.period_start >= date_from,
            AdvertisingStatistic.period_end <= date_to,
        )
    )
    return float(total or 0)


def _has_any_manual_ad_stats(db: Session, *, store_id: str) -> bool:
    return db.scalar(select(AdvertisingStatistic.id).where(AdvertisingStatistic.store_id == store_id).limit(1)) is not None


def _review_stats(db: Session, *, store_id: str, date_from: date, date_to: date) -> tuple[int, float | None]:
    # published_at is a timezone-aware DateTime; date_from/date_to are plain
    # dates — bracket the whole day in UTC, same convention as elsewhere in
    # this app (e.g. search_query_stats_scheduler).
    start = datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(date_to, datetime.max.time(), tzinfo=timezone.utc)
    rows = db.execute(
        select(func.count(Review.id), func.avg(Review.rating)).where(
            Review.store_id == store_id, Review.published_at >= start, Review.published_at <= end
        )
    ).one()
    count = int(rows[0] or 0)
    avg_rating = float(rows[1]) if rows[1] is not None else None
    return count, avg_rating


def _has_any_reviews(db: Session, *, store_id: str) -> bool:
    return db.scalar(select(Review.id).where(Review.store_id == store_id).limit(1)) is not None


def _sum_order_daily_stats(db: Session, *, store_id: str, date_from: date, date_to: date) -> dict:
    """Also carries ordered_units/ordered_sum_discounted_rub (used by the
    orders_revenue block below when this source is available), alongside
    the delivered_*/commission/cost fields the margin block has always used
    — one query serves both, since both read the same table."""
    row = db.execute(
        select(
            func.coalesce(func.sum(OrderDailyStatistic.ordered_units), 0),
            func.coalesce(func.sum(OrderDailyStatistic.ordered_sum_discounted_rub), 0),
            func.coalesce(func.sum(OrderDailyStatistic.delivered_units), 0),
            func.coalesce(func.sum(OrderDailyStatistic.delivered_sum_rub), 0),
            func.coalesce(func.sum(OrderDailyStatistic.commission_rub), 0),
            func.coalesce(func.sum(OrderDailyStatistic.cost_of_delivered_rub), 0),
            func.coalesce(func.sum(OrderDailyStatistic.cost_of_delivered_known_units), 0),
        ).where(
            OrderDailyStatistic.store_id == store_id,
            OrderDailyStatistic.date >= date_from,
            OrderDailyStatistic.date <= date_to,
        )
    ).one()
    return {
        "ordered_units": int(row[0]),
        "ordered_sum_discounted_rub": float(row[1]),
        "delivered_units": int(row[2]),
        "delivered_sum_rub": float(row[3]),
        "commission_rub": float(row[4]),
        "cost_of_delivered_rub": float(row[5]),
        "cost_of_delivered_known_units": int(row[6]),
    }


def _has_any_order_daily_stats(db: Session, *, store_id: str) -> bool:
    return db.scalar(select(OrderDailyStatistic.id).where(OrderDailyStatistic.store_id == store_id).limit(1)) is not None


def _sum_product_stock(db: Session, *, store_id: str) -> tuple[int, int]:
    row = db.execute(
        select(
            func.coalesce(func.sum(Product.fbo_stock), 0),
            func.coalesce(func.sum(Product.fbs_stock), 0),
        ).where(Product.store_id == store_id, Product.is_archived.is_(False))
    ).one()
    return int(row[0]), int(row[1])


def _has_any_product_stock_data(db: Session, *, store_id: str) -> bool:
    # fbo_stock/fbs_stock are only ever set by the catalog sync (POST
    # /v3/product/info/list) — a store that has never run it has both NULL
    # on every product, same "never synced" signal used elsewhere in this
    # file (_has_any_order_daily_stats etc.).
    return (
        db.scalar(
            select(Product.id).where(
                Product.store_id == store_id,
                Product.is_archived.is_(False),
                (Product.fbo_stock.isnot(None)) | (Product.fbs_stock.isnot(None)),
            ).limit(1)
        )
        is not None
    )


def compute_dashboard(
    db: Session,
    *,
    store_id: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> DashboardOut:
    settings = get_settings()
    today = datetime.now(timezone.utc).date()
    resolved_date_to = date_to or today
    resolved_date_from = date_from or (resolved_date_to - timedelta(days=settings.DASHBOARD_DEFAULT_LOOKBACK_DAYS - 1))

    period_days = (resolved_date_to - resolved_date_from).days + 1
    previous_date_to = resolved_date_from - timedelta(days=1)
    previous_date_from = previous_date_to - timedelta(days=period_days - 1)

    # --- Orders / revenue: prefers the automatic Ozon Seller API source
    # (OrderDailyStatistic, same table as "РНП"/"Маржа") over the manual
    # "Аналитика → Товары" CSV import (ProductCardStatistic) whenever the
    # store has any auto-collected order data at all — never both at once,
    # since summing them would double-count the same underlying sales. Both
    # report "заказано" (order time), not "выкуплено" (delivery time, shown
    # separately in the Маржа block) — switching source does not change what
    # the number means, only where it comes from.
    has_order_daily_stats = _has_any_order_daily_stats(db, store_id=store_id)
    has_product_card_data = _has_any_product_card_stats(db, store_id=store_id)
    order_stats_current: dict | None = None

    if has_order_daily_stats:
        order_stats_current = _sum_order_daily_stats(db, store_id=store_id, date_from=resolved_date_from, date_to=resolved_date_to)
        order_stats_previous = _sum_order_daily_stats(db, store_id=store_id, date_from=previous_date_from, date_to=previous_date_to)
        orders_current = order_stats_current["ordered_units"]
        revenue_current = order_stats_current["ordered_sum_discounted_rub"]
        buyout_pct = (
            round(order_stats_current["delivered_units"] / orders_current * 100, 2) if orders_current else None
        )
        orders_revenue = OrdersRevenueBlock(
            has_data=True,
            source="ozon_seller_api",
            orders=_compare(orders_current, order_stats_previous["ordered_units"]),
            revenue_rub=_compare(revenue_current, order_stats_previous["ordered_sum_discounted_rub"]),
            avg_order_value_rub=round(revenue_current / orders_current, 2) if orders_current else None,
            buyout_pct=buyout_pct,
        )
    elif has_product_card_data:
        orders_current, revenue_current, bought_out_current = _sum_product_card_stats(
            db, store_id=store_id, date_from=resolved_date_from, date_to=resolved_date_to
        )
        orders_previous, revenue_previous, _ = _sum_product_card_stats(
            db, store_id=store_id, date_from=previous_date_from, date_to=previous_date_to
        )
        buyout_pct = round(bought_out_current / orders_current * 100, 2) if orders_current else None
        orders_revenue = OrdersRevenueBlock(
            has_data=True,
            source="csv_import",
            orders=_compare(orders_current, orders_previous),
            revenue_rub=_compare(revenue_current, revenue_previous),
            avg_order_value_rub=round(revenue_current / orders_current, 2) if orders_current else None,
            buyout_pct=buyout_pct,
        )
    else:
        revenue_current = 0.0
        orders_revenue = OrdersRevenueBlock(has_data=False)

    # --- Advertising spend (both sources, kept separate) ---
    has_auto_ads = _has_any_auto_ad_stats(db, store_id=store_id)
    has_manual_ads = _has_any_manual_ad_stats(db, store_id=store_id)
    spend_auto_metric = None
    spend_manual_metric = None
    total_spend_current = 0.0
    if has_auto_ads:
        auto_current = _sum_auto_ad_spend(db, store_id=store_id, date_from=resolved_date_from, date_to=resolved_date_to)
        auto_previous = _sum_auto_ad_spend(db, store_id=store_id, date_from=previous_date_from, date_to=previous_date_to)
        spend_auto_metric = _compare(auto_current, auto_previous)
        total_spend_current += auto_current
    if has_manual_ads:
        manual_current = _sum_manual_ad_spend(db, store_id=store_id, date_from=resolved_date_from, date_to=resolved_date_to)
        manual_previous = _sum_manual_ad_spend(db, store_id=store_id, date_from=previous_date_from, date_to=previous_date_to)
        spend_manual_metric = _compare(manual_current, manual_previous)
        total_spend_current += manual_current

    spend_share_of_revenue_pct = None
    if (has_auto_ads or has_manual_ads) and orders_revenue.has_data and revenue_current:
        spend_share_of_revenue_pct = round(total_spend_current / revenue_current * 100, 2)

    advertising = AdvertisingBlock(
        has_data=has_auto_ads or has_manual_ads,
        spend_auto_rub=spend_auto_metric,
        spend_manual_rub=spend_manual_metric,
        spend_share_of_revenue_pct=spend_share_of_revenue_pct,
    )

    # --- Reviews ---
    has_reviews = _has_any_reviews(db, store_id=store_id)
    if has_reviews:
        new_current, avg_current = _review_stats(db, store_id=store_id, date_from=resolved_date_from, date_to=resolved_date_to)
        new_previous, avg_previous = _review_stats(db, store_id=store_id, date_from=previous_date_from, date_to=previous_date_to)
        without_reply = db.scalar(
            select(func.count(Review.id)).where(
                Review.store_id == store_id,
                Review.existing_seller_reply.is_(None),
                Review.status != ReviewStatus.PUBLISHED,
            )
        )
        reviews = ReviewsBlock(
            has_data=True,
            new_count=_compare(new_current, new_previous),
            avg_rating_current=round(avg_current, 2) if avg_current is not None else None,
            avg_rating_previous=round(avg_previous, 2) if avg_previous is not None else None,
            without_reply_count=int(without_reply or 0),
        )
    else:
        reviews = ReviewsBlock(has_data=False)

    # --- Margin (commission/cost/margin — the one block sourced purely via
    # Ozon Seller API postings, see OrderDailyStatistic's own docstring) ---
    if has_order_daily_stats:
        stats = order_stats_current  # already fetched above for orders_revenue — same table, same period
        cost_known = stats["delivered_units"] > 0 and stats["cost_of_delivered_known_units"] >= stats["delivered_units"]
        margin_rub = None
        margin_pct = None
        if cost_known:
            margin_rub = round(
                stats["delivered_sum_rub"] + stats["commission_rub"] - stats["cost_of_delivered_rub"] - total_spend_current, 2
            )
            margin_pct = round(margin_rub / stats["delivered_sum_rub"] * 100, 2) if stats["delivered_sum_rub"] else None
        margin = MarginBlock(
            has_data=True,
            delivered_units=stats["delivered_units"],
            delivered_sum_rub=round(stats["delivered_sum_rub"], 2),
            commission_rub=round(stats["commission_rub"], 2),
            cost_of_delivered_rub=round(stats["cost_of_delivered_rub"], 2) if cost_known else None,
            cost_known=cost_known,
            margin_rub=margin_rub,
            margin_pct=margin_pct,
        )
    else:
        margin = MarginBlock(has_data=False)

    # --- Inventory (current stock snapshot, not period-scoped — see
    # InventoryBlock's own docstring) ---
    has_stock_data = _has_any_product_stock_data(db, store_id=store_id)
    if has_stock_data:
        fbo_units, fbs_units = _sum_product_stock(db, store_id=store_id)
        inventory = InventoryBlock(has_data=True, total_units=fbo_units + fbs_units, fbo_units=fbo_units, fbs_units=fbs_units)
    else:
        inventory = InventoryBlock(has_data=False)

    return DashboardOut(
        period_start=resolved_date_from,
        period_end=resolved_date_to,
        previous_period_start=previous_date_from,
        previous_period_end=previous_date_to,
        orders_revenue=orders_revenue,
        advertising=advertising,
        reviews=reviews,
        margin=margin,
        inventory=inventory,
    )
