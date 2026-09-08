"""Store-wide daily dashboard — the at-a-glance summary sellers/owners check
every day (orders, revenue, ad spend, reviews), as opposed to the per-module
pages (Реклама, Товары, Отзывы) that go deep on one data source at a time.

Every block below is independently has_data — this page combines THREE
separate, already-existing data sources (product-card CSV import, both
advertising sources, reviews) that a given store may or may not have
populated yet, and never fabricates a number for one that hasn't."""
from datetime import date

from pydantic import BaseModel


class DashboardMetric(BaseModel):
    """current vs. the previous period of equal length (not day-over-day —
    a store-wide dashboard period is usually weeks/months, not a single
    day). direction is None only when previous is 0/None (nothing to
    compare against), never fabricated from a zero baseline."""

    current: float
    previous: float | None
    delta_pct: float | None
    direction: str | None  # "up" | "down" | None


class OrdersRevenueBlock(BaseModel):
    """From ProductCardStatistic ("Аналитика → Товары" import) — the only
    source in this app with genuine store-wide daily orders/revenue across
    every product, not just what's advertised. has_data is False if the
    store has never uploaded that report."""

    has_data: bool
    orders: DashboardMetric | None = None
    revenue_rub: DashboardMetric | None = None
    avg_order_value_rub: float | None = None  # revenue / orders for the current period only


class AdvertisingBlock(BaseModel):
    """Ad spend from both sources this app tracks, kept separate (same rule
    as the Реклама page: summing auto-collected and CSV-uploaded spend risks
    double-counting if both cover the same campaigns/period). spend_share_of_
    revenue_pct is a distinct metric from the per-campaign ДРР shown on the
    Реклама page: it divides TOTAL ad spend (both sources) by TOTAL store
    revenue (all channels, from orders_revenue), not by ad-attributed sales
    alone — the number sellers usually mean by "какой процент выручки уходит
    на рекламу". Requires orders_revenue.has_data; null otherwise."""

    has_data: bool
    spend_auto_rub: DashboardMetric | None = None
    spend_manual_rub: DashboardMetric | None = None
    spend_share_of_revenue_pct: float | None = None


class ReviewsBlock(BaseModel):
    has_data: bool
    new_count: DashboardMetric | None = None
    avg_rating_current: float | None = None
    avg_rating_previous: float | None = None
    without_reply_count: int | None = None  # current backlog snapshot, not scoped to the period


class DashboardOut(BaseModel):
    period_start: date
    period_end: date
    previous_period_start: date
    previous_period_end: date
    orders_revenue: OrdersRevenueBlock
    advertising: AdvertisingBlock
    reviews: ReviewsBlock
