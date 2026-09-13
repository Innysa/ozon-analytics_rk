"""Syncs Ozon's official monthly settlement report (POST /v2/finance/
realization) — see app.models.realization_report_month.RealizationReportMonth
for the confirmed request shape and what's still unconfirmed about the
response. Archives the RAW response per (store, year, month); does not
compute commission_rub/delivered_sum_rub yet (see that model's own
docstring for why, and what's needed before this can feed the Дашборд's
MarginBlock — currently only MarginBlock.is_preliminary reacts to this
report's existence, per the confirmed "closed months only" limitation).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.models.realization_report_month import RealizationReportMonth
from app.services.ozon.exceptions import OzonAPIError, OzonFeatureUnavailable


@dataclass
class RealizationSyncOutcome:
    fetched: bool = False
    created: bool = False  # only meaningful when fetched is True: new row vs. updated an existing one
    # Ozon 404 "Report was not found" — CONFIRMED EXPECTED for a month
    # still in progress (see RealizationReportMonth's own docstring), not
    # a failure to alert on. Distinguished from `error` so callers can
    # treat it as an ordinary "not ready yet" outcome.
    not_yet_available: bool = False
    error: str | None = None  # any other real OzonAPIError — genuinely worth surfacing


def _closed_months_before(*, today: date, count: int) -> list[tuple[int, int]]:
    """Returns (year, month) for the `count` calendar months immediately
    BEFORE today's month, most recent first — NEVER includes today's own
    (still open, per Ozon's own confirmed 404) month."""
    months: list[tuple[int, int]] = []
    year, month = today.year, today.month
    for _ in range(count):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        months.append((year, month))
    return months


def sync_realization_report_month(db: Session, *, store_id: str, client, year: int, month: int) -> RealizationSyncOutcome:
    """Fetches ONE month's report and upserts it. Safe to call for a month
    that's already archived (overwrites with a fresh copy) or for the
    still-open current month (returns not_yet_available=True instead of
    raising — this is Ozon's own confirmed, expected answer for that
    case, not an error condition)."""
    try:
        data = client.get_realization_report(year=year, month=month)
    except OzonFeatureUnavailable as exc:
        return RealizationSyncOutcome(not_yet_available=True, error=str(exc))
    except OzonAPIError as exc:
        return RealizationSyncOutcome(error=str(exc))

    raw_json = json.dumps(data, ensure_ascii=False)
    existing = (
        db.query(RealizationReportMonth)
        .filter(
            RealizationReportMonth.store_id == store_id,
            RealizationReportMonth.year == year,
            RealizationReportMonth.month == month,
        )
        .first()
    )
    if existing:
        existing.raw_payload = raw_json
        db.commit()
        return RealizationSyncOutcome(fetched=True, created=False)
    db.add(RealizationReportMonth(store_id=store_id, year=year, month=month, raw_payload=raw_json, source="ozon_seller_api"))
    db.commit()
    return RealizationSyncOutcome(fetched=True, created=True)


def sync_missing_realization_reports(
    db: Session, *, store_id: str, client, backfill_months: int, today: date | None = None
) -> list[tuple[int, int, RealizationSyncOutcome]]:
    """Tries every one of the last `backfill_months` CLOSED months that
    doesn't already have a RealizationReportMonth row for this store —
    idempotent (never re-fetches an already-archived month), and safe to
    call daily or on demand: a month Ozon still says isn't ready simply
    comes back with not_yet_available=True, to be retried next time."""
    resolved_today = today or date.today()
    results: list[tuple[int, int, RealizationSyncOutcome]] = []
    for year, month in _closed_months_before(today=resolved_today, count=backfill_months):
        already_have = (
            db.query(RealizationReportMonth.id)
            .filter(
                RealizationReportMonth.store_id == store_id,
                RealizationReportMonth.year == year,
                RealizationReportMonth.month == month,
            )
            .first()
        )
        if already_have:
            continue
        results.append((year, month, sync_realization_report_month(db, store_id=store_id, client=client, year=year, month=month)))
    return results
