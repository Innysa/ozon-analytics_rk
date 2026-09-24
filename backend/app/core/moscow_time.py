"""Shared Moscow-time helpers. Russia has used a single fixed UTC+3
nationwide since 2014 — no DST, no tz database needed, a plain timedelta
offset is exact and never goes stale.

Centralized here (rather than each caller defining its own local offset)
because several independent day-boundary decisions across this app need to
agree on the SAME "what calendar day is it in Moscow right now" answer:
order_daily_sync_service's own in_process_at bucketing (Ozon's cabinet
reports per-day counts in Moscow local time, not UTC — see that module's
own _parse_in_process_at docstring for the full story) and, ИЗМЕНЕНО
2026-09-24, product_planner_service/routes.orders' funnel-vs-postings
"Заказано" override, which must never treat TODAY's still-accumulating
funnel row as more complete than postings — see moscow_today()'s own
docstring below for why that specific case matters.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

MSK_OFFSET = timedelta(hours=3)


def utc_to_moscow_date(dt: datetime) -> date:
    """Converts a timezone-aware UTC datetime to the Moscow calendar date it
    falls on."""
    return (dt + MSK_OFFSET).date()


def moscow_today() -> date:
    """Today's date in Moscow, computed from the current UTC instant. Used
    to tell a CLOSED day (yesterday or earlier, whose funnel figure has had
    all day/night to be computed by Ozon) apart from the still-in-progress
    current day, whose funnel row — even when one already exists — only
    reflects however much of today Ozon had processed by the last sync
    attempt, not the full day. CONFIRMED as a real gap 2026-09-24: a
    real account's own diagnostic dump (backend/scripts/show_product_
    analytics_daily.py) showed the funnel's ordered_units for the CURRENT
    day at 1, while postings for the very same day already stood at 9 —
    the funnel row existed (so the old "prefer it whenever it exists" rule
    picked it), it just wasn't finished yet."""
    return utc_to_moscow_date(datetime.now(timezone.utc))
