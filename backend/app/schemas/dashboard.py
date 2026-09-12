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
    """Ad spend from three sources this app tracks, kept separate (same rule
    as the Реклама page: summing them risks double-counting if two cover the
    same campaigns/period). spend_share_of_revenue_pct is a distinct metric
    from the per-campaign ДРР shown on the Реклама page: it divides TOTAL ad
    spend (all sources) by TOTAL store revenue (all channels, from
    orders_revenue), not by ad-attributed sales alone — the number sellers
    usually mean by "какой процент выручки уходит на рекламу". Requires
    orders_revenue.has_data; null otherwise.

    spend_other_formats_rub: ad spend types Ozon bills that
    AdvertisingDailyStatistic (Performance API's statistics-report) never
    captures at all, regardless of campaign state — CONFIRMED 2026-09-12 on
    a real account by matching exact rub amounts between a manually
    exported Ozon "Начисления" report and cash-flow's own services bucket:
    CPO-style "Продвижение с оплатой за заказ" promotions
    (MarketplaceServicePromotionWithCostPerOrder) and seller-funded bonus
    mailings (MarketplaceServiceItemElectronicServicesPremiumSellerBonusAccrual).
    This — not a sync bug — was most of a ~21% gap between the Дашборд and
    Ozon's own cabinet that survived fixing the earlier campaign-state
    exclusion bug. Sourced from CashFlowStatementPeriod, so it carries the
    same day-count-prorated "estimate" caveat as LogisticsBlock for a
    period only partially in range — deliberately not flagged with its own
    is_estimated here (this app has only ever surfaced that alongside
    LogisticsBlock so far); treat as approximate the same way. None (not
    0) when no cash-flow period overlaps the range at all, i.e. "unknown",
    not "confirmed zero"."""

    has_data: bool
    spend_auto_rub: DashboardMetric | None = None
    spend_manual_rub: DashboardMetric | None = None
    spend_other_formats_rub: DashboardMetric | None = None
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
    transaction/list. Ozon groups its own weekly periods that rarely align
    to the dashboard's selected range — a period counting only if FULLY
    contained in it (still the rule for AdvertisingStatistic's
    period_start/period_end, where it's correct) silently undercounted
    here, so a period that only PARTIALLY overlaps is now included with
    its figures scaled by the fraction of its own days inside the range
    (see is_estimated below); period_note names the periods actually
    summed so this isn't silent either way.

    logistics_rub = sum of delivery.delivery_services.total (Ozon's own
    subtotal — real components confirmed: last-mile courier, dropoff,
    handover to Ozon, "direct flow" logistics). returns_logistics_rub =
    sum of return.return_services.total ONLY — "return" is a TOP-LEVEL
    sibling of delivery, not nested inside it (an earlier guess had it as
    delivery.return and was wrong), and its own total/amount pair is NOT
    usable directly: return.amount is the base monetary VALUE of returned
    orders (a revenue-return figure, not a cost — mirrors delivery.amount,
    which is likewise excluded from logistics_rub above), and return.total
    = amount + return_services.total, so using it double-counts a figure
    that doesn't belong in "Услуги доставки" at all. Real cost, confirmed:
    MarketplaceServiceItemReturnFlowLogistic — "Обратная логистика", the
    cost of shipping a returned item back — see CashFlowStatementPeriod's
    own docstring for the full correction history.

    services.total is a MIXED bucket (storage + advertising cost-per-click
    + insurance + fines + possibly more) with no per-category subtotal from
    Ozon — only a lump total plus a raw items[] list. storage_rub/fines_rub
    are pulled OUT of that total by confirmed real item-name substrings
    (see dashboard_service.py's _categorize_service_items): "Storage" ->
    storage_rub, "Fine" -> fines_rub (both CONFIRMED present, 2026-09-10 —
    MarketplaceServiceItemTemporaryStorageRedistribution and
    FinesShipmentNonRecommendedSlot respectively). other_services_rub is
    the REMAINDER (services_total - fines_rub - storage_rub), not summed
    independently from items[] — so a period whose items weren't recorded,
    or that has an item name this matching doesn't recognize, still keeps
    its money in other_services_rub instead of it silently disappearing.

    other_deductions_rub = sum of the separate "others" bucket inside
    details[] (also {"total", "items[]"}, confirmed 2026-09-10 — e.g.
    acquiring fees, seller "decompensation") — NOT sub-split, only two
    item names observed so far.

    other_services_top_item_name/_rub — the single largest (by absolute
    price) uncategorized item across the summed periods, e.g. a real
    observed case: "MarketplaceServiseItemAgencyFeeForSale" (sic — that
    typo is Ozon's own, not this codebase's) swinging from -2 787 193.76
    to +8 764 167.68 across different weeks, confirmed 2026-09-10 to be
    genuine volatile Ozon data (not a sync bug) after inspecting
    raw_payload. A single such item can dominate other_services_rub and
    look like an error — surfacing which item it was (without guessing an
    arbitrary "significant %" threshold, which would need a number this
    project has no basis to pick) lets a seller check for themselves
    rather than mistake it for a bug. None when there are no uncategorized
    items to show.

    Commission is deliberately NOT repeated here — MarginBlock.commission_
    rub (from postings' financial_data) is the one already shown on the
    dashboard, and this endpoint's own commission_amount has not been
    confirmed to match it number-for-number, so showing both would risk
    two conflicting "commission" figures without an explanation of why
    they might differ.

    is_estimated: a period only counts toward this block if it OVERLAPS
    the requested range at all (changed 2026-09-12 from requiring FULL
    containment, which silently undercounted any range not aligned to
    Ozon's own week boundaries — confirmed on a real account: a 12-day
    range matched only one 6-day period fully and showed less than half
    the real total with no indication anything was missing). A period that
    only partially overlaps has its figures scaled by the fraction of its
    own days that fall in range — a linear day-count ESTIMATE, since Ozon
    gives no way to get a genuine per-day split of a period's totals. True
    whenever at least one summed period was partial, so the frontend can
    mark the numbers as approximate rather than presenting them as exact."""

    has_data: bool
    logistics_rub: float | None = None
    returns_logistics_rub: float | None = None
    storage_rub: float | None = None
    fines_rub: float | None = None
    other_deductions_rub: float | None = None
    other_services_rub: float | None = None
    other_services_top_item_name: str | None = None
    other_services_top_item_rub: float | None = None
    periods_summed: int = 0
    is_estimated: bool = False
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
