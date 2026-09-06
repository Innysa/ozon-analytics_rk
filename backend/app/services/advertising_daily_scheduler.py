"""Wires an in-process APScheduler job that runs the automatic advertising
daily-statistics sync (app.services.advertising_daily_sync_service) once a
day for every store that has Ozon Performance API credentials configured.

This app has no external task queue (no Celery/RQ, no cron container) — an
in-process BackgroundScheduler was chosen deliberately over relying on the
deployment's own crontab, because the requirement was fully automatic
operation with no manual step per deployment: this starts and stops with the
same `uvicorn app.main:app` process the app already runs as, so there is
nothing extra for a seller (or whoever deploys this) to configure.

Never started under ENV=test — pytest's TestClient(app) fixture triggers
FastAPI startup/shutdown events on every test, and a scheduler thread has no
useful role in a test run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.encryption import decrypt_secret
from app.db.session import SessionLocal
from app.models.ozon_credentials import OzonCredentials
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.services.advertising_daily_sync_service import sync_advertising_daily_statistics
from app.services.audit import record_audit
from app.services.ozon_performance.client import OzonPerformanceClient
from app.services.ozon_performance.client import PerformanceCredentials as OzonPerfCredentials
from app.services.ozon_performance.exceptions import OzonPerformanceAPIError, OzonPerformanceAuthError

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_one_store(db: Session, creds: OzonCredentials) -> None:
    store_id = creds.store_id
    run = SyncRun(
        store_id=store_id,
        source_type=SyncSourceType.OZON_ADVERTISING_STATISTICS_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    db.commit()

    client_id = decrypt_secret(creds.performance_client_id_encrypted)
    client_secret = decrypt_secret(creds.performance_client_secret_encrypted)
    error_message = None
    try:
        with OzonPerformanceClient(OzonPerfCredentials(client_id=client_id, client_secret=client_secret)) as client:
            outcome = sync_advertising_daily_statistics(db, store_id=store_id, client=client)
        run.items_fetched = outcome.fetched
        run.items_created = outcome.created
        run.items_skipped_duplicate = outcome.updated
        error_message = "; ".join(outcome.errors[:20]) if outcome.errors else None
        run.status = SyncStatus.SUCCESS if not outcome.errors else (
            SyncStatus.PARTIAL if (outcome.created or outcome.updated) else SyncStatus.FAILED
        )
    except OzonPerformanceAuthError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except OzonPerformanceAPIError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except Exception as exc:  # a SyncRun must never be left stuck "running" forever
        run.status = SyncStatus.FAILED
        error_message = f"Внутренняя ошибка: {exc}"
        logger.exception("Реклама: непредвиденная ошибка планового автосбора статистики, store_id=%s", store_id)

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


def run_advertising_daily_statistics_for_all_stores() -> None:
    """The scheduled job body: one sync per store that has Performance API
    credentials, run sequentially (never in parallel — Ozon's own "1 report
    in flight" limit is per account, but running stores one at a time also
    keeps a single crashed store from starving DB connections from the
    others)."""
    db = SessionLocal()
    try:
        creds_list = (
            db.query(OzonCredentials)
            .filter(
                OzonCredentials.performance_client_id_encrypted.isnot(None),
                OzonCredentials.performance_client_secret_encrypted.isnot(None),
            )
            .all()
        )
        logger.info("Реклама: плановый автосбор статистики — магазинов с ключами Performance API: %d", len(creds_list))
        for creds in creds_list:
            _run_one_store(db, creds)
    finally:
        db.close()


def start_advertising_daily_statistics_scheduler() -> BackgroundScheduler | None:
    settings = get_settings()
    if settings.ENV == "test" or not settings.ADVERTISING_STATS_SCHEDULER_ENABLED:
        return None

    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_advertising_daily_statistics_for_all_stores,
        CronTrigger(hour=settings.ADVERTISING_STATS_SCHEDULER_HOUR_UTC, minute=0),
        id="advertising_daily_statistics_sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info(
        "Реклама: планировщик автосбора статистики запущен (ежедневно в %02d:00 UTC)",
        settings.ADVERTISING_STATS_SCHEDULER_HOUR_UTC,
    )
    return _scheduler


def stop_advertising_daily_statistics_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
