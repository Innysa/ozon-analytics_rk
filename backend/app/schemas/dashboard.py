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
    """Store-wide daily orders/revenue across every product, not just what's
    advertised. Two possible sources, NEVER combined (that would double-count
    the same sales) — the automatic one is preferred whenever the store has
    any of it at all:
      - source="ozon_seller_api": OrderDailyStatistic (same table as "РНП"/
        "Маржа"), collected automatically via Ozon Seller API postings.
      - source="csv_import": ProductCardStatistic, from the manually
        uploaded "Аналитика → Товары" report — used only when the store has
        no automatic order data yet.
    Both report "заказано" (order time, not delivery time) — orders and
    revenue here are NOT the same figures as "Выкуплено"/"Выручка (выкуп)"
    in MarginBlock below, which is delivery-time and excludes cancellations.
    has_data is False only when the store has neither source at all."""

    has_data: bool
    source: str | None = None  # "ozon_seller_api" | "csv_import" | None (no data)
    orders: DashboardMetric | None = None
    revenue_rub: DashboardMetric | None = None
    avg_order_value_rub: float | None = None  # revenue / orders for the current period only
    buyout_pct: float | None = None  # delivered (bought-out) units / ordered units for the current period, from the SAME active source above


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


class MarginBlock(BaseModel):
    """Комиссия/себестоимость/маржа — sourced from OrderDailyStatistic
    (Ozon Seller API postings, see that model's own docstring), the one
    genuinely API-only source among the dashboard's blocks. margin_rub/
    margin_pct are null whenever cost_known is False — a margin computed
    with a missing cost price would just be wrong, not merely approximate,
    so this never falls back to treating an unset cost as 0."""

    has_data: bool
    delivered_units: int | None = None
    delivered_sum_rub: float | None = None
    commission_rub: float | None = None  # as Ozon reports it — negative (a deduction)
    cost_of_delivered_rub: float | None = None
    cost_known: bool | None = None  # whether cost price was set for every delivered unit in the period
    margin_rub: float | None = None  # delivered_sum_rub + commission_rub - cost_of_delivered_rub - реклама (оба источника)
    margin_pct: float | None = None


class InventoryBlock(BaseModel):
    """Total remaining stock (Ozon warehouses only, FBO+FBS), from the
    product catalog sync (Product.fbo_stock/fbs_stock, populated by
    POST /v3/product/info/list via the "Синхронизировать с Ozon" button on
    the «Товары» page) — a CURRENT snapshot, not scoped to the selected
    period (same convention as ReviewsBlock.without_reply_count above).
    Archived products are excluded. has_data is False only if the store has
    never synced its catalog from Ozon at all."""

    has_data: bool
    total_units: int | None = None
    fbo_units: int | None = None
    fbs_units: int | None = None


class LogisticsBlock(BaseModel):
    """Логистика/Хранение/Прочие удержания — sourced from
    CashFlowStatementPeriod (Ozon Seller API POST /v1/finance/cash-flow-
    statement/list, see that model's own docstring for the confirmed
    contract), the replacement for the now-obsolete /v3/finance/
    transaction/list. Ozon groups its own weekly periods — a period counts
    toward the dashboard's selected range only if FULLY contained in it
    (same rule already used for AdvertisingStatistic's period_start/
    period_end), so a short or misaligned custom range can show has_data
    False or partial coverage even when periods exist nearby; period_note
    names the periods actually summed so this isn't silent.

    logistics_rub = sum of delivery.delivery_services.total (Ozon's own
    subtotal — real components confirmed: last-mile courier, dropoff,
    handover to Ozon, "direct flow" logistics). returns_logistics_rub =
    sum of delivery.return.total (return processing, e.g. via a pickup
    point). other_services_rub = sum of services.total — a MIXED bucket
    (storage + advertising cost-per-click + insurance + possibly more,
    confirmed from real item names) that Ozon does NOT break into
    Хранение/Штрафы separately through this method; shown as one honest
    lump sum rather than a guessed split. Commission is deliberately NOT
    repeated here — MarginBlock.commission_rub (from postings' financial_
    data) is the one already shown on the dashboard, and this endpoint's
    own commission_amount has not been confirmed to match it number-for-
    number, so showing both would risk two conflicting "commission"
    figures without an explanation of why they might differ."""

    has_data: bool
    logistics_rub: float | None = None
    returns_logistics_rub: float | None = None
    other_services_rub: float | None = None
    periods_summed: int = 0
    period_note: str | None = None  # e.g. "2026-08-17 — 2026-09-06 (3 периода Ozon)"


class DashboardOut(BaseModel):
    period_start: date
    period_end: date
    previous_period_start: date
    previous_period_end: date
    orders_revenue: OrdersRevenueBlock
    advertising: AdvertisingBlock
    reviews: ReviewsBlock
    margin: MarginBlock
    inventory: InventoryBlock
    logistics: LogisticsBlock
