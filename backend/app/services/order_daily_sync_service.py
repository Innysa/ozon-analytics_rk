"""Orchestrates the automatic daily orders sync from Ozon Seller API's
postings endpoints (FBO: POST /v2/posting/fbo/list, FBS: POST
/v3/posting/fbs/list) — the "РНП" data source, and the first ingredient of
margin/ROI on the Дашборд. See app.models.order_daily_statistic's module
docstring for the confirmed field-level contract.

Confirmed via backend/scripts/debug_orders_finance_api.py against a real
account (473-line CSV bugs earlier in this project are exactly why this
discipline exists — see that script's own docstring):

  1. Both endpoints accept `filter: {since, to}` (full ISO-8601 timestamps,
     same "...T00:00:00Z" convention already used for
     get_product_query_details) plus `offset`/`limit` pagination and
     `with: {financial_data}` (`analytics_data` dropped 2026-09-11 — this
     module never reads it, and it was extra weight on a call that turned
     out to be prone to sustained 429s on a real account; see
     OzonSellerClient._post()'s own retry-decorator comment). Response is
     `{"result": {"postings": [...], "has_next": bool}}` for both endpoints
     — CONFIRMED (OzonPostingListResponse's `result: ... | list[...] |
     None` union exists only as a defensive fallback in case a future
     response comes back as a bare list; it hasn't been observed).
  2. `has_next` pagination is REAL — a real account's finance-transactions
     call (same style of pagination) came back with exactly `page_size`
     rows for a 7-day window, meaning more rows existed beyond that page.
     _fetch_all_postings() below loops on `has_next` rather than assuming
     one page is everything, capped at MAX_PAGES as a runaway guard.
  3. Each posting's `products[]` entry has `sku`/`quantity`/`price` (the
     actual sale price *after* any discount, e.g. "1200.00", a string) and
     a matching entry in `financial_data.products[]` — joined by
     `product_id` == the product's `sku` — carrying `old_price` (before
     discount), `commission_amount`, `commission_percent`, `payout`.
  4. `status` has confirmed real values "delivered" and "cancelled". Ozon
     has many more granular statuses (e.g. "awaiting_deliver",
     "acceptance_in_progress") that this module does NOT yet distinguish —
     _bucket_status() below buckets everything that isn't literally
     "delivered" or "cancelled" as "unfinished". This is a deliberate
     simplification, not an oversight: refining it needs a confirmed list
     of every status Ozon actually uses, which hasn't been gathered yet.
  5. commission_amount's summed here as-is per product line. Whether it
     already accounts for quantity > 1 (i.e. is a total for the line) or
     is a flat per-unit figure is UNCONFIRMED — every real example seen so
     far had quantity=1. Treated as a per-line total (NOT multiplied by
     quantity again) until a multi-quantity real example says otherwise.

NOT covered by this sync (see README for the full status):
  - Impressions/cart-adds/conversion funnel (Количество переходов в
    карточку / Положили в корзину / Конверсия) — postings carry no such
    data; that funnel is only available via the manual "Аналитика →
    Товары" CSV upload (ProductCardStatistic) today.
  - Logistics/storage/penalties/taxes breakdown — lives in Ozon's Finance
    API (/v3/finance/transaction/list), not postings. A real account's
    transactions there had a "other" type covering 37% of all operations
    with no confirmed example of what it contains — building anything on
    it needs one more confirmed diagnostic round first (see
    backend/scripts/debug_orders_finance_api.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.order_daily_statistic import OrderDailyStatistic
from app.models.product import Product
from app.models.product_order_daily_statistic import ProductOrderDailyStatistic
from app.services.ozon.exceptions import OzonAPIError
from app.services.ozon.schemas import OzonPostingItem

MAX_PAGES = 50
PAGE_LIMIT = 1000


@dataclass
class SyncOutcome:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)
    # Postings whose in_process_at was empty at sync time — aggregate_postings_by_day()/
    # aggregate_postings_by_sku_and_day() silently drop these from every day's totals
    # (see _parse_in_process_at's own callers). NOT an error — likely just an order that
    # hasn't entered processing at Ozon's warehouse yet, so its true date isn't knowable
    # yet — but worth surfacing on every run (success included), not just discoverable via
    # a one-off diagnostic script (see backend/scripts/debug_sku_order_dates.py, added
    # 2026-09-11 investigating exactly this).
    skipped_no_process_date: int = 0


def _to_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_in_process_at(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _bucket_status(status: str | None) -> str:
    if status == "delivered":
        return "delivered"
    if status == "cancelled":
        return "cancelled"
    return "unfinished"


def _financial_line_for_sku(financial_data: dict | None, sku: int | None) -> dict:
    if not financial_data or sku is None:
        return {}
    for line in financial_data.get("products", []) or []:
        if line.get("product_id") == sku:
            return line
    return {}


def _empty_bucket() -> dict:
    return {
        "ordered_units": 0, "ordered_sum_rub": 0.0, "ordered_sum_discounted_rub": 0.0,
        "delivered_units": 0, "delivered_sum_rub": 0.0,
        "cost_of_delivered_rub": 0.0, "cost_of_delivered_known_units": 0,
        "cancelled_units": 0, "cancelled_sum_rub": 0.0,
        "unfinished_units": 0, "commission_rub": 0.0,
    }


def aggregate_postings_by_day(postings: list[OzonPostingItem], *, cost_by_sku: dict[str, float]) -> dict[date, dict]:
    """Pure aggregation over already-fetched postings — kept separate from
    the HTTP fetch loop so it's testable without a fake client."""
    daily: dict[date, dict] = {}
    for posting in postings:
        day = _parse_in_process_at(posting.in_process_at)
        if day is None:
            continue
        bucket = daily.setdefault(day, _empty_bucket())
        status_bucket = _bucket_status(posting.status)

        for product in posting.products:
            qty = product.quantity or 0
            if qty <= 0:
                continue
            price = _to_float(product.price)
            fin_line = _financial_line_for_sku(posting.financial_data, product.sku)
            old_price = _to_float(fin_line["old_price"]) if fin_line.get("old_price") is not None else price
            commission = _to_float(fin_line.get("commission_amount"))

            bucket["ordered_units"] += qty
            bucket["ordered_sum_rub"] += old_price * qty
            bucket["ordered_sum_discounted_rub"] += price * qty
            bucket["commission_rub"] += commission

            if status_bucket == "delivered":
                bucket["delivered_units"] += qty
                bucket["delivered_sum_rub"] += price * qty
                cost = cost_by_sku.get(str(product.sku)) if product.sku is not None else None
                if cost is not None:
                    bucket["cost_of_delivered_rub"] += cost * qty
                    bucket["cost_of_delivered_known_units"] += qty
            elif status_bucket == "cancelled":
                bucket["cancelled_units"] += qty
                bucket["cancelled_sum_rub"] += price * qty
            else:
                bucket["unfinished_units"] += qty

    return daily


def _empty_sku_bucket() -> dict:
    return {
        "ordered_units": 0, "ordered_sum_rub": 0.0, "ordered_sum_discounted_rub": 0.0,
        "delivered_units": 0, "delivered_sum_rub": 0.0,
        "cancelled_units": 0, "cancelled_sum_rub": 0.0,
        "unfinished_units": 0, "commission_rub": 0.0,
    }


def aggregate_postings_by_sku_and_day(postings: list[OzonPostingItem]) -> dict[tuple[str, date], dict]:
    """Same aggregation as aggregate_postings_by_day, but keyed by (sku, day)
    instead of just day — the per-line sku that function discards after
    computing cost/commission. No extra Ozon call: same already-fetched
    postings, just not thrown away this time. Deliberately excludes cost
    (see ProductOrderDailyStatistic's own docstring — margin stays a
    store-level concern)."""
    daily: dict[tuple[str, date], dict] = {}
    for posting in postings:
        day = _parse_in_process_at(posting.in_process_at)
        if day is None:
            continue
        status_bucket = _bucket_status(posting.status)

        for product in posting.products:
            qty = product.quantity or 0
            if qty <= 0 or product.sku is None:
                continue
            sku = str(product.sku)
            bucket = daily.setdefault((sku, day), _empty_sku_bucket())
            price = _to_float(product.price)
            fin_line = _financial_line_for_sku(posting.financial_data, product.sku)
            old_price = _to_float(fin_line["old_price"]) if fin_line.get("old_price") is not None else price
            commission = _to_float(fin_line.get("commission_amount"))

            bucket["ordered_units"] += qty
            bucket["ordered_sum_rub"] += old_price * qty
            bucket["ordered_sum_discounted_rub"] += price * qty
            bucket["commission_rub"] += commission

            if status_bucket == "delivered":
                bucket["delivered_units"] += qty
                bucket["delivered_sum_rub"] += price * qty
            elif status_bucket == "cancelled":
                bucket["cancelled_units"] += qty
                bucket["cancelled_sum_rub"] += price * qty
            else:
                bucket["unfinished_units"] += qty

    return daily


def _apply_sku_bucket(stat: ProductOrderDailyStatistic, bucket: dict) -> None:
    stat.ordered_units = bucket["ordered_units"]
    stat.ordered_sum_rub = bucket["ordered_sum_rub"]
    stat.ordered_sum_discounted_rub = bucket["ordered_sum_discounted_rub"]
    stat.delivered_units = bucket["delivered_units"]
    stat.delivered_sum_rub = bucket["delivered_sum_rub"]
    stat.cancelled_units = bucket["cancelled_units"]
    stat.cancelled_sum_rub = bucket["cancelled_sum_rub"]
    stat.unfinished_units = bucket["unfinished_units"]
    stat.commission_rub = bucket["commission_rub"]


def _apply_bucket(stat: OrderDailyStatistic, bucket: dict) -> None:
    stat.ordered_units = bucket["ordered_units"]
    stat.ordered_sum_rub = bucket["ordered_sum_rub"]
    stat.ordered_sum_discounted_rub = bucket["ordered_sum_discounted_rub"]
    stat.delivered_units = bucket["delivered_units"]
    stat.delivered_sum_rub = bucket["delivered_sum_rub"]
    stat.cost_of_delivered_rub = bucket["cost_of_delivered_rub"]
    stat.cost_of_delivered_known_units = bucket["cost_of_delivered_known_units"]
    stat.cancelled_units = bucket["cancelled_units"]
    stat.cancelled_sum_rub = bucket["cancelled_sum_rub"]
    stat.unfinished_units = bucket["unfinished_units"]
    stat.commission_rub = bucket["commission_rub"]


def skipped_no_process_date_note(outcome: SyncOutcome) -> str | None:
    """A one-line, human-readable note for SyncRun.error_message when
    outcome.skipped_no_process_date > 0 — surfaced on EVERY run (success
    included, not just partial/failed) so this doesn't require a one-off
    diagnostic script to notice (see SyncOutcome.skipped_no_process_date's
    own docstring for why these postings get dropped)."""
    if not outcome.skipped_no_process_date:
        return None
    return (
        f"Пропущено отправлений без даты in_process_at (ещё не в обработке у Ozon, "
        f"дата появится позже): {outcome.skipped_no_process_date}"
    )


def _fetch_all_postings(fetch_fn, *, date_from: str, date_to: str) -> list[OzonPostingItem]:
    all_postings: list[OzonPostingItem] = []
    offset = 0
    for _ in range(MAX_PAGES):
        response = fetch_fn(date_from=date_from, date_to=date_to, offset=offset, limit=PAGE_LIMIT)
        result = response.result
        if result is None:
            break
        postings = result if isinstance(result, list) else result.postings
        if not postings:
            break
        all_postings.extend(postings)
        has_next = False if isinstance(result, list) else bool(result.has_next)
        if not has_next:
            break
        offset += PAGE_LIMIT
    return all_postings


def _date_chunks(date_from: date, date_to: date, chunk_days: int) -> list[tuple[date, date]]:
    """Splits [date_from, date_to] into consecutive (start, end) pairs of at
    most chunk_days each — see ORDER_STATS_SYNC_CHUNK_DAYS's own comment for
    why this exists. chunk_days <= 0 would loop forever, so it's floored at
    1 rather than trusted blindly (this is a config value, not user input,
    but a bad deploy value should degrade to "many small chunks", not hang)."""
    chunks: list[tuple[date, date]] = []
    step = max(chunk_days, 1)
    start = date_from
    while start <= date_to:
        end = min(start + timedelta(days=step - 1), date_to)
        chunks.append((start, end))
        start = end + timedelta(days=1)
    return chunks


def sync_order_daily_statistics(
    db: Session,
    *,
    store_id: str,
    client,
    date_from: date | None = None,
    date_to: date | None = None,
) -> SyncOutcome:
    """Fetches FBO + FBS postings for the period, aggregates per day, and
    upserts into OrderDailyStatistic. Commits once per fulfillment scheme
    (FBO, then FBS), so one failing entirely doesn't lose the other.

    The requested [date_from, date_to] window is itself split into smaller
    `since`/`to` chunks (ORDER_STATS_SYNC_CHUNK_DAYS) before ever calling
    Ozon — one call covering the full 30-day window turned out to trigger
    intermittent sustained 429s on a real account's FBO postings that even
    an 8-attempt/Retry-After-aware retry couldn't reliably survive (see
    OzonSellerClient._post()'s own retry decorator comment for that
    incident). Each chunk is fetched (with its own has_next pagination, same
    as before) and failures are per-chunk: one chunk exhausting retries adds
    a note to outcome.errors and is skipped, but does NOT discard postings
    already fetched from other chunks in the same run — a real improvement
    over the old all-or-nothing per-schema behavior, not just smaller
    requests for their own sake."""
    settings = get_settings()
    outcome = SyncOutcome()

    today = datetime.now(timezone.utc).date()
    resolved_date_to = date_to or today
    resolved_date_from = date_from or (resolved_date_to - timedelta(days=settings.ORDER_STATS_DEFAULT_LOOKBACK_DAYS - 1))
    chunks = _date_chunks(resolved_date_from, resolved_date_to, settings.ORDER_STATS_SYNC_CHUNK_DAYS)

    cost_by_sku = {
        sku: float(cost)
        for sku, cost in db.query(Product.ozon_sku, Product.cost_price_rub)
        .filter(Product.store_id == store_id, Product.cost_price_rub.isnot(None))
        .all()
    }

    for schema_label, fetch_fn in (("FBO", client.list_fbo_postings), ("FBS", client.list_fbs_postings)):
        postings: list[OzonPostingItem] = []
        for chunk_from, chunk_to in chunks:
            chunk_from_ts = f"{chunk_from.isoformat()}T00:00:00Z"
            chunk_to_ts = f"{chunk_to.isoformat()}T23:59:59Z"
            try:
                postings.extend(_fetch_all_postings(fetch_fn, date_from=chunk_from_ts, date_to=chunk_to_ts))
            except OzonAPIError as exc:
                outcome.errors.append(f"{schema_label} {chunk_from.isoformat()}—{chunk_to.isoformat()}: {exc}")
                continue

        outcome.fetched += len(postings)
        outcome.skipped_no_process_date += sum(1 for p in postings if _parse_in_process_at(p.in_process_at) is None)
        daily = aggregate_postings_by_day(postings, cost_by_sku=cost_by_sku)

        for day, bucket in daily.items():
            existing = (
                db.query(OrderDailyStatistic)
                .filter(
                    OrderDailyStatistic.store_id == store_id,
                    OrderDailyStatistic.date == day,
                    OrderDailyStatistic.delivery_schema == schema_label,
                )
                .first()
            )
            if existing:
                _apply_bucket(existing, bucket)
                outcome.updated += 1
            else:
                stat = OrderDailyStatistic(store_id=store_id, date=day, delivery_schema=schema_label, source="ozon_seller_api")
                _apply_bucket(stat, bucket)
                db.add(stat)
                outcome.created += 1
        db.commit()

        # Same already-fetched postings, additionally broken down per SKU —
        # no extra Ozon call (see aggregate_postings_by_sku_and_day).
        by_sku_day = aggregate_postings_by_sku_and_day(postings)
        for (sku, day), bucket in by_sku_day.items():
            existing_sku = (
                db.query(ProductOrderDailyStatistic)
                .filter(
                    ProductOrderDailyStatistic.store_id == store_id,
                    ProductOrderDailyStatistic.ozon_sku == sku,
                    ProductOrderDailyStatistic.date == day,
                    ProductOrderDailyStatistic.delivery_schema == schema_label,
                )
                .first()
            )
            if existing_sku:
                _apply_sku_bucket(existing_sku, bucket)
            else:
                sku_stat = ProductOrderDailyStatistic(
                    store_id=store_id, ozon_sku=sku, date=day, delivery_schema=schema_label, source="ozon_seller_api",
                )
                _apply_sku_bucket(sku_stat, bucket)
                db.add(sku_stat)
        db.commit()

    return outcome
