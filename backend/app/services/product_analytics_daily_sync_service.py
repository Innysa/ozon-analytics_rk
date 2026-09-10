"""Orchestrates the automatic per-product funnel sync from Ozon Seller API's
POST /v1/analytics/data (dimension=["sku", "day"]) — see the CONFIRMED
request/response contract in app.services.ozon.client.OzonSellerClient
.get_analytics_data() and app.services.ozon.schemas.OzonAnalyticsDataResponse
(confirmed against a real account with Premium Plus, 2026-09-10).

Requires Ozon Premium Plus/Premium Pro. Without it, this method only exposes
revenue/ordered_units (already covered by the free order_daily_statistics
sync) — what Ozon actually does with a Premium-only metric requested by a
non-Premium account (omit it, zero it, or reject the whole call) is
UNCONFIRMED and NOT specially handled here: a sync attempt on such a store
simply fails with whatever error Ozon returns, surfaced as a failed SyncRun
rather than guessed at or silently degraded.

_METRICS below is the FIXED, ordered list of metrics this app always
requests. The response's per-row `metrics` array is POSITIONAL (see the
client method's docstring) — this exact order must never change without
also updating _row_to_record()'s indices to match.

Rate limit: Ozon allows only 1 request/minute to this method (confirmed in
the official docs). A lookback window whose (sku, day) row count exceeds the
1000-row page size therefore pages through `offset`, sleeping
PRODUCT_ANALYTICS_STATS_RATE_LIMIT_SLEEP_SECONDS (60s) between pages — never
firing a second request within the same minute. PRODUCT_ANALYTICS_STATS_
MAX_PAGES bounds this loop: a store whose real row count for the window
exceeds MAX_PAGES * 1000 gets a documented partial result (outcome.errors
names how many rows were left unfetched) rather than an unbounded run.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.product_analytics_daily_statistic import ProductAnalyticsDailyStatistic
from app.services.ozon.exceptions import OzonAPIError
from app.services.ozon.schemas import OzonAnalyticsDataRow

PAGE_LIMIT = 1000

_METRICS = [
    "revenue", "ordered_units",
    "hits_view_pdp", "hits_tocart_pdp", "conv_tocart_pdp", "session_view_pdp", "position_category",
]


@dataclass
class SyncOutcome:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)


def _row_to_record(row: OzonAnalyticsDataRow) -> dict | None:
    """Pure parsing of one confirmed-shape row — skips (returns None for) a
    row that doesn't match the expected shape rather than crashing the whole
    sync over one bad row."""
    if len(row.dimensions) < 2 or len(row.metrics) < len(_METRICS):
        return None
    sku = row.dimensions[0].id
    day_str = row.dimensions[1].id
    if not sku or not day_str:
        return None
    try:
        day = date.fromisoformat(day_str)
    except ValueError:
        return None

    m = row.metrics
    return {
        "ozon_sku": sku,
        "date": day,
        "revenue_rub": m[0],
        "ordered_units": int(m[1]),
        "views_pdp": int(m[2]),
        "cart_adds_pdp": int(m[3]),
        "cart_conversion_pdp_pct": m[4],
        "sessions_pdp": int(m[5]),
        "position_category": m[6],
    }


def _fetch_all_analytics_rows(
    client, *, date_from: str, date_to: str, max_pages: int, rate_limit_sleep_s: int
) -> tuple[list[OzonAnalyticsDataRow], bool]:
    """Pages through offset while a page comes back full (== PAGE_LIMIT rows),
    sleeping rate_limit_sleep_s between requests to respect Ozon's 1
    request/minute cap on this method. Returns (rows, truncated) — truncated
    is True only when max_pages was hit with a still-full last page, meaning
    more rows likely exist beyond what was fetched."""
    all_rows: list[OzonAnalyticsDataRow] = []
    offset = 0
    truncated = False
    for page in range(max_pages):
        if page > 0:
            time.sleep(rate_limit_sleep_s)
        response = client.get_analytics_data(
            date_from=date_from, date_to=date_to, dimension=["sku", "day"], metrics=_METRICS,
            limit=PAGE_LIMIT, offset=offset,
        )
        rows = response.result.data if response.result else []
        all_rows.extend(rows)
        if len(rows) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
        if page == max_pages - 1:
            truncated = True
    return all_rows, truncated


def sync_product_analytics_daily_statistics(
    db: Session,
    *,
    store_id: str,
    client,
    date_from: date | None = None,
    date_to: date | None = None,
) -> SyncOutcome:
    """Fetches per-(sku, day) funnel rows for the period (paging through
    offset if the window has more than PAGE_LIMIT rows, see
    _fetch_all_analytics_rows) and upserts into ProductAnalyticsDailyStatistic."""
    settings = get_settings()
    outcome = SyncOutcome()

    today = datetime.now(timezone.utc).date()
    resolved_date_to = date_to or today
    resolved_date_from = date_from or (resolved_date_to - timedelta(days=settings.PRODUCT_ANALYTICS_STATS_DEFAULT_LOOKBACK_DAYS - 1))

    try:
        rows, truncated = _fetch_all_analytics_rows(
            client,
            date_from=resolved_date_from.isoformat(),
            date_to=resolved_date_to.isoformat(),
            max_pages=settings.PRODUCT_ANALYTICS_STATS_MAX_PAGES,
            rate_limit_sleep_s=settings.PRODUCT_ANALYTICS_STATS_RATE_LIMIT_SLEEP_SECONDS,
        )
    except OzonAPIError as exc:
        outcome.errors.append(str(exc))
        return outcome

    outcome.fetched = len(rows)
    if truncated:
        outcome.errors.append(
            f"Данных за период больше, чем удалось получить за {settings.PRODUCT_ANALYTICS_STATS_MAX_PAGES} "
            f"запросов ({outcome.fetched} строк) — часть строк не загружена. "
            "Уменьшите период синхронизации или увеличьте PRODUCT_ANALYTICS_STATS_MAX_PAGES."
        )

    for row in rows:
        record = _row_to_record(row)
        if record is None:
            continue
        existing = (
            db.query(ProductAnalyticsDailyStatistic)
            .filter(
                ProductAnalyticsDailyStatistic.store_id == store_id,
                ProductAnalyticsDailyStatistic.ozon_sku == record["ozon_sku"],
                ProductAnalyticsDailyStatistic.date == record["date"],
            )
            .first()
        )
        if existing:
            for key, value in record.items():
                if key not in ("ozon_sku", "date"):
                    setattr(existing, key, value)
            outcome.updated += 1
        else:
            db.add(ProductAnalyticsDailyStatistic(store_id=store_id, source="ozon_seller_api", **record))
            outcome.created += 1
    db.commit()

    return outcome
