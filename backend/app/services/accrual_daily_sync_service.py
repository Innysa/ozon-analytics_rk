"""Syncs Ozon's true daily accrual total (POST /v1/finance/accrual/by-day)
— see app.models.accrual_daily_statistic.AccrualDailyStatistic for the
confirmed contract and why only the whole-day total and the coarse
accrued_category split are computed here, not a "Комиссия Ozon"-specific
figure yet.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.accrual_daily_statistic import AccrualDailyStatistic
from app.services.ozon.exceptions import OzonAPIError

PAGE_SIZE = 1000
# Runaway guard, not a confirmed cap — no real day on the one account this
# was tested against needed more than a handful of pages.
MAX_PAGES = 50


def _to_num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


@dataclass
class AccrualSyncOutcome:
    fetched: bool = False
    created: bool = False  # only meaningful when fetched is True
    record_count: int = 0
    total_amount_rub: float = 0.0
    error: str | None = None


def _find_record_list(obj):
    """Recursively finds the first NON-EMPTY list whose elements are
    dicts — the real top-level key holding the records array (e.g.
    "result.rows" vs "result.records" vs something else) is NOT confirmed
    for this method, so this doesn't hardcode one — same generic walker
    (and same "non-empty" requirement, to avoid mismatching an unrelated
    empty array elsewhere in the response) used by the diagnostic scripts
    that first confirmed this endpoint's real shape
    (backend/scripts/probe_accrual_and_realization.py,
    lookup_accrual_records.py). Returns None both when nothing list-shaped
    exists AND when a day genuinely has zero records — _fetch_all_records
    below treats both the same way: stop, nothing more to add."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj
    if isinstance(obj, dict):
        for value in obj.values():
            found = _find_record_list(value)
            if found is not None:
                return found
    return None


def _fetch_all_records(client, day_str: str) -> list[dict]:
    """Loops pages while the page comes back exactly PAGE_SIZE long — the
    real pagination field name (e.g. page_count/has_next) isn't confirmed
    for this method, so this avoids hardcoding one, same as the diagnostic
    scripts that first confirmed this endpoint's shape. A day with zero
    records (_find_record_list finds nothing) correctly yields an empty
    list here, not an error — see that helper's own docstring."""
    all_records: list[dict] = []
    page = 1
    while page <= MAX_PAGES:
        data = client.get_accrual_by_day(day=day_str, page=page, page_size=PAGE_SIZE)
        records = _find_record_list(data)
        if records is None:
            break
        all_records.extend(records)
        if len(records) < PAGE_SIZE:
            break
        page += 1
    return all_records


def sync_accrual_daily_statistic(db: Session, *, store_id: str, client, day: date) -> AccrualSyncOutcome:
    """Fetches and upserts ONE day's total. Safe to call repeatedly for the
    same day (idempotent overwrite) — Ozon can revise recent accruals
    after the fact, so re-syncing a short trailing window daily (see
    sync_recent_accrual_days below) is intentional, not wasted work."""
    try:
        records = _fetch_all_records(client, day.isoformat())
    except OzonAPIError as exc:
        return AccrualSyncOutcome(error=str(exc))

    total = 0.0
    by_category: dict[str, float] = {}
    for r in records:
        amount = _to_num((r.get("total_amount") or {}).get("amount"))
        category = r.get("accrued_category") or "(без категории)"
        by_category[category] = round(by_category.get(category, 0.0) + amount, 2)
        total += amount
    total = round(total, 2)

    existing = (
        db.query(AccrualDailyStatistic)
        .filter(AccrualDailyStatistic.store_id == store_id, AccrualDailyStatistic.date == day)
        .first()
    )
    created = existing is None
    if existing is None:
        existing = AccrualDailyStatistic(store_id=store_id, date=day, source="ozon_seller_api")
        db.add(existing)
    existing.total_amount_rub = total
    existing.by_category_json = json.dumps(by_category, ensure_ascii=False)
    existing.record_count = len(records)
    db.commit()

    return AccrualSyncOutcome(fetched=True, created=created, record_count=len(records), total_amount_rub=total)


def sync_recent_accrual_days(
    db: Session, *, store_id: str, client, days: int, today: date | None = None
) -> list[tuple[date, AccrualSyncOutcome]]:
    """Re-syncs each of the last `days` days BEFORE today (never today
    itself — an in-progress day's accruals are still accumulating, same
    "not final yet" reasoning as MarginBlock.is_preliminary). Always
    re-fetches even already-archived days in this window (unlike the
    realization-report backfill, which skips what's already archived) —
    CONFIRMED motivation: Ozon can revise a recent day's accruals after
    the fact (e.g. a late return), so only the CURRENT run's numbers are
    trusted as final for days inside this trailing window."""
    resolved_today = today or date.today()
    results: list[tuple[date, AccrualSyncOutcome]] = []
    for offset in range(1, days + 1):
        day = resolved_today - timedelta(days=offset)
        results.append((day, sync_accrual_daily_statistic(db, store_id=store_id, client=client, day=day)))
    return results
