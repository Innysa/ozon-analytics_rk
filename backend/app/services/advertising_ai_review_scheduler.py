"""Wires an in-process APScheduler job that generates the automatic
AI-written advertising-campaign review (app.services.advertising_ai_review_
service) once a day for every store that has Ozon Performance API
credentials configured — the same gating signal app.services.advertising_
daily_scheduler uses, since a store with no Performance API keys has no
automatically-collected daily statistics for this to analyze anyway. Same
in-process-scheduler architecture as advertising_daily_scheduler and
search_query_stats_scheduler, for the same reason (no external task queue in
this app).

This app has no job-chaining/orchestration mechanism — each scheduled sync
is an independent daily cron job, not a pipeline with a "run B after A
completes" primitive. "Right after the daily ad-stats sync" is therefore
approximated as a fixed daily time (see settings.ADVERTISING_AI_REVIEW_
SCHEDULER_HOUR_UTC, default 4:30 UTC — after both the 3:00 UTC ad-stats sync
and the 4:00 UTC search-query-stats sync), not a literal completion trigger.
If the ad-stats sync is still running past that point for some store on a
given day, this run simply analyzes whatever AdvertisingDailyStatistic rows
already exist in the configured lookback window — nothing breaks, and the
next day's run picks up any gap.

Never started under ENV=test — see advertising_daily_scheduler's own
docstring for why.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.ozon_credentials import OzonCredentials
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.services.advertising_ai_review_service import generate_advertising_ai_review
from app.services.ai.factory import get_ai_provider
from app.services.audit import record_audit

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_one_store(db: Session, store_id: str) -> None:
    run = SyncRun(
        store_id=store_id,
        source_type=SyncSourceType.OZON_ADVERTISING_AI_REVIEW,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    db.commit()

    error_message = None
    try:
        ai_provider = get_ai_provider()
        outcome = generate_advertising_ai_review(db, store_id=store_id, ai_provider=ai_provider)
        run.items_fetched = outcome.campaigns_analyzed
        run.items_created = 1 if outcome.saved else 0
        error_message = "; ".join(outcome.errors[:20]) if outcome.errors else None
        run.status = (
            SyncStatus.SUCCESS if outcome.saved and not outcome.errors
            else (SyncStatus.PARTIAL if outcome.saved else SyncStatus.FAILED)
        )
    except Exception as exc:  # a SyncRun must never be left stuck "running" forever
        run.status = SyncStatus.FAILED
        error_message = f"Внутренняя ошибка: {exc}"
        logger.exception("Реклама AI-обзор: непредвиденная ошибка планового запуска, store_id=%s", store_id)

    run.finished_at = datetime.now(timezone.utc)
    run.error_message = error_message
    record_audit(
        db,
        action="sync_finished",
        store_id=store_id,
        target_type="sync_run",
        target_id=run.id,
        result="success" if run.status == SyncStatus.SUCCESS else "failure",
        message=error_message,
    )
    db.commit()


def run_advertising_ai_review_for_all_stores() -> None:
    """The scheduled job body: one AI-review generation per store that has
    Ozon Performance API credentials configured, run sequentially (keeps a
    single crashed/slow store from starving DB connections or AI-provider
    rate limits from the others)."""
    db = SessionLocal()
    try:
        store_ids = [
            row[0]
            for row in db.query(OzonCredentials.store_id)
            .filter(
                OzonCredentials.performance_client_id_encrypted.isnot(None),
                OzonCredentials.performance_client_secret_encrypted.isnot(None),
            )
            .all()
        ]
        logger.info("Реклама AI-обзор: плановый запуск — магазинов с ключами Performance API: %d", len(store_ids))
        for store_id in store_ids:
            _run_one_store(db, store_id)
    finally:
        db.close()


def start_advertising_ai_review_scheduler() -> BackgroundScheduler | None:
    settings = get_settings()
    if settings.ENV == "test" or not settings.ADVERTISING_AI_REVIEW_SCHEDULER_ENABLED:
        return None

    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_advertising_ai_review_for_all_stores,
        CronTrigger(hour=settings.ADVERTISING_AI_REVIEW_SCHEDULER_HOUR_UTC, minute=30),
        id="advertising_ai_review",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info(
        "Реклама AI-обзор: планировщик запущен (ежедневно в %02d:30 UTC)",
        settings.ADVERTISING_AI_REVIEW_SCHEDULER_HOUR_UTC,
    )
    return _scheduler


def stop_advertising_ai_review_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
