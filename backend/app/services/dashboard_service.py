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

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
from app.models.advertising_statistic import AdvertisingStatistic
from app.models.cash_flow_statement_period import CashFlowStatementPeriod
from app.models.order_daily_statistic import OrderDailyStatistic
from app.models.product import Product
from app.models.product_card_statistic import ProductCardStatistic
from app.models.review import Review, ReviewStatus
from app.schemas.dashboard import (
    AdvertisingBlock,
    DashboardMetric,
    DashboardOut,
    InventoryBlock,
    LogisticsBlock,
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


def _manual_ad_spend_incomplete(db: Session, *, store_id: str, date_from: date, date_to: date) -> bool:
    """True when at least one AdvertisingStatistic row OVERLAPS [date_from,
    date_to] without being fully contained in it — meaning
    _sum_manual_ad_spend's total for this exact window is a known
    UNDERCOUNT (real spend from a straddling period excluded, per that
    function's own "can't be split" rule), not the true total for the
    window.

    CONFIRMED root cause (2026-09-11) of a real account's Дашборд showing
    an absurd "+1070%" jump in "Расход (загружено вручную)" for a recent
    period vs. the immediately preceding one: the PREVIOUS-period window
    compute_dashboard() compares against is synthesized (same length,
    immediately before) — the user never chose it and can't align it to
    their own upload periods, unlike the current period. A manually
    uploaded row spanning (e.g.) most of August straddled that synthetic
    boundary, got fully excluded, and left a small-but-nonzero total from
    whatever other rows happened to fit — a real number, just not the
    real total, which read as a huge fake swing once compared to a normal
    current-period total. Used by compute_dashboard() to suppress the
    delta_pct/direction comparison (not the current-period figure itself)
    whenever the baseline it would compare against is known-incomplete —
    same "never fabricate a comparison" spirit as DashboardMetric's
    existing zero-baseline rule, extended to a known-partial one."""
    return (
        db.scalar(
            select(AdvertisingStatistic.id).where(
                AdvertisingStatistic.store_id == store_id,
                AdvertisingStatistic.period_start <= date_to,
                AdvertisingStatistic.period_end >= date_from,
                or_(AdvertisingStatistic.period_start < date_from, AdvertisingStatistic.period_end > date_to),
            ).limit(1)
        )
        is not None
    )


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


def _cash_flow_periods_overlapping(db: Session, *, store_id: str, date_from: date, date_to: date) -> list[CashFlowStatementPeriod]:
    """Every stored period that overlaps [date_from, date_to] AT ALL — not
    just ones fully contained in it. Ozon's own weekly periods rarely align
    to an arbitrary dashboard range; the OLD "fully contained only" rule
    (same one still used for AdvertisingStatistic's period_start/period_end
    above, where it's the right call — see that model's own docstring)
    meant a range not aligned to Ozon's own week boundaries could silently
    show a fraction of the real Логистика/Хранение/etc figure with no
    indication anything was missing. CONFIRMED on a real account
    (2026-09-12): a 12-day range (2026-09-01..09-12) matched only ONE
    6-day Ozon period fully, showing "Логистика и услуги" as 203 183.5 ₽
    against a real (Ozon's own cabinet) total of 421 643 ₽ for the same
    12 days — a ~52% UNDERCOUNT with no warning. compute_dashboard() now
    prorates each returned period's figures by _period_overlap_fraction()
    instead of requiring full containment, and marks the result
    is_estimated whenever any period only partially overlaps — see there."""
    return list(
        db.scalars(
            select(CashFlowStatementPeriod).where(
                CashFlowStatementPeriod.store_id == store_id,
                CashFlowStatementPeriod.period_begin <= date_to,
                CashFlowStatementPeriod.period_end >= date_from,
            ).order_by(CashFlowStatementPeriod.period_begin)
        )
    )


def _period_overlap_fraction(period_begin: date, period_end: date, date_from: date, date_to: date) -> float:
    """What fraction of an Ozon cash-flow period's own span falls inside
    [date_from, date_to] — 1.0 for a period fully contained in the range,
    less for one that straddles an edge. Ozon gives no way to get a
    genuinely per-day split of a period's totals (see
    CashFlowStatementPeriod's own docstring), so this is a LINEAR estimate
    by day count, not a real per-day figure — real spend within a period
    is very unlikely to be spread evenly across its days. Good enough to
    turn a silent, unbounded undercount into a labeled, roughly-right
    number (see LogisticsBlock.is_estimated), not a substitute for a real
    per-day source if one is ever confirmed."""
    period_days = (period_end - period_begin).days + 1
    if period_days <= 0:
        return 0.0
    overlap_start = max(period_begin, date_from)
    overlap_end = min(period_end, date_to)
    overlap_days = (overlap_end - overlap_start).days + 1
    return max(0.0, min(1.0, overlap_days / period_days))


# Ad spend types confirmed (2026-09-12, real account: a manually exported
# "Начисления" report matched these exact rub amounts to specific cash-flow
# services_items_json entries) to live INSIDE cash-flow's services bucket
# but NEVER inside AdvertisingDailyStatistic (Performance API's statistics-
# report only covers pay-per-click/impression campaigns) — the two together
# explained a ~21% ad-spend gap between the Дашборд and Ozon's own cabinet
# that survived the earlier campaign-state fix:
#   - MarketplaceServicePromotionWithCostPerOrder ("Продвижение с оплатой
#     за заказ" — a CPO promotion product, not CPC)
#   - MarketplaceServiceItemElectronicServicesPremiumSellerBonusAccrual
#     ("Бонусы продавца - рассылка" — a seller-funded bonus/mailing promo)
# Deliberately does NOT include MarketplaceServiceCostPerClick here — that
# one IS already captured by AdvertisingDailyStatistic; re-adding it from
# cash-flow too would double-count real CPC spend the Дашборд already shows.
_CASH_FLOW_AD_NAME_SUBSTRINGS = ("PromotionWithCostPerOrder", "PremiumSellerBonusAccrual")


def _cash_flow_matching_items_sum(
    db: Session, *, store_id: str, date_from: date, date_to: date, name_substrings: tuple[str, ...]
) -> float | None:
    """Sum of services_items_json entries whose `name` contains any of
    name_substrings, across every CashFlowStatementPeriod overlapping
    [date_from, date_to] — prorated the same way as LogisticsBlock (see
    _period_overlap_fraction) for a period only partially inside the range.
    Returns None (not 0.0) when NO period overlaps at all — "no cash-flow
    data synced for this window" is a different situation from "data exists
    and genuinely has none of these items", and the caller (compute_dashboard)
    treats them differently (see AdvertisingBlock.spend_other_formats_rub)."""
    periods = _cash_flow_periods_overlapping(db, store_id=store_id, date_from=date_from, date_to=date_to)
    if not periods:
        return None
    total = 0.0
    for p in periods:
        if not p.services_items_json:
            continue
        fraction = _period_overlap_fraction(p.period_begin, p.period_end, date_from, date_to)
        try:
            items = json.loads(p.services_items_json)
        except (TypeError, ValueError):
            continue
        for item in items:
            name = item.get("name") or ""
            if any(s in name for s in name_substrings):
                total += float(item.get("price") or 0) * fraction
    return round(total, 2)


def _has_any_cash_flow_periods(db: Session, *, store_id: str) -> bool:
    return db.scalar(select(CashFlowStatementPeriod.id).where(CashFlowStatementPeriod.store_id == store_id).limit(1)) is not None


# Substrings CONFIRMED (2026-09-12, real account raw_payload — located via
# inspect_cash_flow_periods.py --find-key, cross-checked against a manually
# exported Ozon "Начисления" report's own group names) to belong to
# "Услуги партнёров" и "Услуги FBO".
#
# NOT split by bucket, unlike an earlier version of this constant. "Эквайринг"
# (MarketplaceRedistributionOfAcquiringOperation / the newer ...AcquiringItem
# spelling) and "Страхование товара от массовых повреждений"
# (InsuranceServiceSellerItem) do NOT live in one fixed bucket across
# periods — CONFIRMED 2026-09-12 via --find-key on two different weeks of the
# SAME real account: both items were found inside `details.services.items[]`
# for 2026-09-01–09-06, but inside `details.others.items[]` for
# 2026-09-07–09-13. The earlier version of this code searched InsuranceService
# only in `services` and Acquiring only in `others`, so any period where Ozon
# happened to place either item in the "wrong" (for that hint) bucket silently
# matched nothing for it — this undercounted "Услуги партнёров" (-55 488.03
# shown vs a real ~-117 306 for 1-12.09, per the user's own --find-key output
# and manual per-period sum). Fixed by checking the FULL hint list against
# BOTH buckets.
_PARTNER_SERVICE_HINTS = ("InsuranceService", "AcquiringItem", "AcquiringOperation")
# "Кросс-докинг" (MarketplaceServiceItemCrossdocking, confirmed price matched
# exactly to the Начисления report's own "Кросс-докинг" line) is confirmed to
# live in `services` only so far — no evidence yet of it (or
# SupplyInboundAdditional) appearing in `others`, so it stays single-bucket
# rather than being widened on a guess. Other real sub-items the Начисления
# report showed under these same two groups (partner delivery-to-pickup-point,
# partner packaging, temporary storage BY a partner, FBO inbound/outbound
# handling beyond crossdocking) have NOT been matched to a confirmed raw
# `name` yet — they stay uncategorized rather than being guessed at, same
# discipline as fines/storage below.
_FBO_SERVICE_HINTS_IN_SERVICES = ("Crossdocking", "SupplyInboundAdditional")

# CONFIRMED 2026-09-12 (real account): summing every LogisticsBlock figure
# and comparing to Ozon's own "Услуги и штрафы" total showed a ~+299k gap
# — "Продвижение и реклама" is its OWN group in Ozon's accounting (shown
# on this Дашборд as the separate "Реклама" block), but its cash-flow
# items were never excluded from other_services_rub, so "Прочие услуги"
# silently included real ad spend a seller would already see counted
# elsewhere. Unlike _CASH_FLOW_AD_NAME_SUBSTRINGS (used for the
# Advertising block's spend_other_formats_rub, which deliberately omits
# CostPerClick to avoid a second VISIBLE card double-showing it), this
# list is for EXCLUSION from "Прочие услуги" only — CostPerClick belongs
# here too, since its real spend is already shown via the Performance-API
# -sourced "Расход на рекламу (авто)" card, just from a different source.
_AD_HINTS_TO_EXCLUDE_FROM_OTHER_SERVICES = ("CostPerClick", "PromotionWithCostPerOrder", "PremiumSellerBonusAccrual")


def _categorize_service_items(items_json: str | None) -> tuple[float, float]:
    """Pulls (fines, storage) out of a CashFlowStatementPeriod.
    services_items_json blob, by CONFIRMED real item-name substrings
    (checked against a real account's full diagnostic dump, 2026-09-10):
    "Fine" -> a real fine (e.g. FinesShipmentNonRecommendedSlot), "Storage"
    -> real storage fee (MarketplaceServiceItemTemporaryStorageRedistribution).

    Deliberately does NOT also return an "other" sum computed from the
    remaining items — the caller derives that as (period.services_total -
    fines - storage - ...) instead, so a period whose items_json is empty/
    missing (e.g. an older row synced before with_details was in use)
    still keeps its full services_total in "Прочие услуги" rather than
    silently losing it because there was nothing to scan. Only these two
    substrings have been directly confirmed against a real item name in
    THIS bucket so far — see _sum_matching_items for the partner/FBO
    substrings, which span both `services` and `others`."""
    if not items_json:
        return 0.0, 0.0
    try:
        items = json.loads(items_json)
    except (TypeError, ValueError):
        return 0.0, 0.0
    fines = storage = 0.0
    for item in items:
        name = item.get("name") or ""
        price = float(item.get("price") or 0)
        if "Fine" in name:
            fines += price
        elif "Storage" in name:
            storage += price
    return fines, storage


def _sum_matching_items(items_json: str | None, name_hints: tuple[str, ...]) -> float:
    """Sum of `price` across an items_json blob's entries whose `name`
    contains any of name_hints — generic version of the fines/storage
    matching above, used for hints that must be checked against a
    SPECIFIC bucket (services OR others) rather than always the same one."""
    if not items_json:
        return 0.0
    try:
        items = json.loads(items_json)
    except (TypeError, ValueError):
        return 0.0
    return sum(float(item.get("price") or 0) for item in items if any(h in (item.get("name") or "") for h in name_hints))


def _largest_uncategorized_service_item(periods: list[CashFlowStatementPeriod]) -> tuple[str, float] | None:
    """Finds the single largest (by absolute price) item across all periods'
    services_items_json that _categorize_service_items would leave
    uncategorized (i.e. not matched as a fine/storage item) — surfaced on
    the Дашборд so a large volatile line (confirmed real example,
    2026-09-10: "MarketplaceServiseItemAgencyFeeForSale" swinging from
    -2 787 193.76 to +8 764 167.68 across different weeks on one real
    account) doesn't get mistaken for a sync bug when it dominates
    other_services_rub for a given range. Deliberately no "% of total"
    threshold — always surfaces the single biggest item, since this
    project has no confirmed basis to pick a significance cutoff."""
    best: tuple[str, float] | None = None
    for period in periods:
        if not period.services_items_json:
            continue
        try:
            items = json.loads(period.services_items_json)
        except (TypeError, ValueError):
            continue
        for item in items:
            name = item.get("name") or ""
            if (
                "Fine" in name
                or "Storage" in name
                or any(h in name for h in _PARTNER_SERVICE_HINTS)
                or any(h in name for h in _FBO_SERVICE_HINTS_IN_SERVICES)
                or any(h in name for h in _AD_HINTS_TO_EXCLUDE_FROM_OTHER_SERVICES)
            ):
                continue
            price = item.get("price")
            if price is None:
                continue
            price = float(price)
            if best is None or abs(price) > abs(best[1]):
                best = (name, price)
    return best


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

    # --- Advertising spend (three sources, kept separate) ---
    has_auto_ads = _has_any_auto_ad_stats(db, store_id=store_id)
    has_manual_ads = _has_any_manual_ad_stats(db, store_id=store_id)
    has_cash_flow_data = _has_any_cash_flow_periods(db, store_id=store_id)
    spend_auto_metric = None
    spend_manual_metric = None
    spend_other_metric = None
    total_spend_current = 0.0
    if has_auto_ads:
        auto_current = _sum_auto_ad_spend(db, store_id=store_id, date_from=resolved_date_from, date_to=resolved_date_to)
        auto_previous = _sum_auto_ad_spend(db, store_id=store_id, date_from=previous_date_from, date_to=previous_date_to)
        spend_auto_metric = _compare(auto_current, auto_previous)
        total_spend_current += auto_current
    if has_manual_ads:
        manual_current = _sum_manual_ad_spend(db, store_id=store_id, date_from=resolved_date_from, date_to=resolved_date_to)
        # See _manual_ad_spend_incomplete's own docstring: a straddling upload
        # excluded from the PREVIOUS window's total makes that total a known
        # undercount, not a real (small) baseline — comparing against it
        # produces a fabricated swing, so previous stays None (no delta_pct/
        # direction) rather than showing one. Does NOT affect manual_current
        # itself, which is the user's own chosen window and shown as-is.
        manual_previous = (
            None
            if _manual_ad_spend_incomplete(db, store_id=store_id, date_from=previous_date_from, date_to=previous_date_to)
            else _sum_manual_ad_spend(db, store_id=store_id, date_from=previous_date_from, date_to=previous_date_to)
        )
        spend_manual_metric = _compare(manual_current, manual_previous)
        total_spend_current += manual_current
    if has_cash_flow_data:
        # See _CASH_FLOW_AD_NAME_SUBSTRINGS's own comment: ad spend types
        # cash-flow bills but AdvertisingDailyStatistic never captures at
        # all (CPO promotions, seller bonus mailings) — sign flipped to
        # positive (cash-flow stores these as negative/deductions) to match
        # spend_auto_rub/spend_manual_rub's own convention.
        other_current = _cash_flow_matching_items_sum(
            db, store_id=store_id, date_from=resolved_date_from, date_to=resolved_date_to,
            name_substrings=_CASH_FLOW_AD_NAME_SUBSTRINGS,
        )
        if other_current is not None:
            other_previous = _cash_flow_matching_items_sum(
                db, store_id=store_id, date_from=previous_date_from, date_to=previous_date_to,
                name_substrings=_CASH_FLOW_AD_NAME_SUBSTRINGS,
            )
            spend_other_metric = _compare(-other_current, -other_previous if other_previous is not None else None)
            total_spend_current += -other_current

    spend_share_of_revenue_pct = None
    if (has_auto_ads or has_manual_ads or spend_other_metric is not None) and orders_revenue.has_data and revenue_current:
        spend_share_of_revenue_pct = round(total_spend_current / revenue_current * 100, 2)

    advertising = AdvertisingBlock(
        has_data=has_auto_ads or has_manual_ads or spend_other_metric is not None,
        spend_auto_rub=spend_auto_metric,
        spend_manual_rub=spend_manual_metric,
        spend_other_formats_rub=spend_other_metric,
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

    # --- Logistics/services (Ozon's own weekly cash-flow periods — see
    # LogisticsBlock's own docstring for why this stays separate from
    # MarginBlock rather than being folded into margin_rub: the periods
    # here are Ozon-defined weekly buckets, not the same day-precise window
    # postings use, so combining them into one "more precise" margin figure
    # risked silently mixing two different accounting windows) ---
    # has_cash_flow_data already computed above for the Advertising block.
    if has_cash_flow_data:
        periods_in_range = _cash_flow_periods_overlapping(db, store_id=store_id, date_from=resolved_date_from, date_to=resolved_date_to)
        if periods_in_range:
            fines_sum = storage_sum = services_total_sum = 0.0
            logistics_sum = returns_sum = others_total_sum = 0.0
            partner_from_services_sum = partner_from_others_sum = 0.0
            fbo_from_services_sum = ad_in_services_sum = 0.0
            is_estimated = False
            for p in periods_in_range:
                fraction = _period_overlap_fraction(p.period_begin, p.period_end, resolved_date_from, resolved_date_to)
                if fraction < 1.0:
                    is_estimated = True
                fines, storage = _categorize_service_items(p.services_items_json)
                partner_from_services = _sum_matching_items(p.services_items_json, _PARTNER_SERVICE_HINTS)
                partner_from_others = _sum_matching_items(p.others_items_json, _PARTNER_SERVICE_HINTS)
                fbo_from_services = _sum_matching_items(p.services_items_json, _FBO_SERVICE_HINTS_IN_SERVICES)
                ad_in_services = _sum_matching_items(p.services_items_json, _AD_HINTS_TO_EXCLUDE_FROM_OTHER_SERVICES)

                fines_sum += fines * fraction
                storage_sum += storage * fraction
                partner_from_services_sum += partner_from_services * fraction
                partner_from_others_sum += partner_from_others * fraction
                fbo_from_services_sum += fbo_from_services * fraction
                ad_in_services_sum += ad_in_services * fraction
                services_total_sum += float(p.services_total or 0) * fraction
                logistics_sum += float(p.delivery_services_total or 0) * fraction
                returns_sum += float(p.delivery_return_total or 0) * fraction
                others_total_sum += float(p.others_total or 0) * fraction
            partner_services_sum = partner_from_services_sum + partner_from_others_sum
            # other_services_rub / other_deductions_rub are each the
            # REMAINDER of their own bucket's total after pulling out the
            # portion that landed in THAT bucket specifically — not summed
            # independently from items[] — so a period whose items_json
            # doesn't (fully) cover its own total (e.g. an older row, or an
            # Ozon item name this matching hasn't seen yet) never drops
            # that money silently. Real ad spend (ad_in_services_sum) is
            # excluded here too — it's a DIFFERENT Ozon group
            # ("Продвижение и реклама"), already represented by the
            # separate Реклама block, not by anything in this one — see
            # _AD_HINTS_TO_EXCLUDE_FROM_OTHER_SERVICES's own comment.
            other_services_sum = (
                services_total_sum - fines_sum - storage_sum - partner_from_services_sum
                - fbo_from_services_sum - ad_in_services_sum
            )
            other_deductions_sum = others_total_sum - partner_from_others_sum
            # Deliberately the RAW (non-prorated) item — this is an
            # informational "which item dominates" pointer, not a total
            # this range claims to own; prorating it would misrepresent a
            # real observed Ozon amount as an estimate it isn't.
            top_item = _largest_uncategorized_service_item(periods_in_range)
            logistics = LogisticsBlock(
                has_data=True,
                logistics_rub=round(logistics_sum, 2),
                returns_logistics_rub=round(returns_sum, 2),
                storage_rub=round(storage_sum, 2),
                fines_rub=round(fines_sum, 2),
                partner_services_rub=round(partner_services_sum, 2),
                fbo_services_rub=round(fbo_from_services_sum, 2),
                other_deductions_rub=round(other_deductions_sum, 2),
                other_services_rub=round(other_services_sum, 2),
                other_services_top_item_name=top_item[0] if top_item else None,
                other_services_top_item_rub=round(top_item[1], 2) if top_item else None,
                periods_summed=len(periods_in_range),
                is_estimated=is_estimated,
                period_note=(
                    f"{periods_in_range[0].period_begin} — {periods_in_range[-1].period_end} "
                    f"({len(periods_in_range)} период{'' if len(periods_in_range) == 1 else 'а' if len(periods_in_range) < 5 else 'ов'} Ozon"
                    + (", часть периодов не совпадает с диапазоном — суммы оценочные (пропорционально дням)" if is_estimated else "")
                    + ")"
                ),
            )
        else:
            # Store has synced periods, but none even OVERLAP this specific
            # range (e.g. cash-flow hasn't synced that far, or a range with
            # no Ozon activity at all) — has_data True with zero
            # periods_summed, not a false "no data at all" — the frontend
            # distinguishes these via periods_summed.
            logistics = LogisticsBlock(has_data=True, periods_summed=0, period_note="Нет периодов Ozon, пересекающихся с выбранным диапазоном")
    else:
        logistics = LogisticsBlock(has_data=False)

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
        logistics=logistics,
    )
