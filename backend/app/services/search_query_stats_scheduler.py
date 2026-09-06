"""Wires an in-process APScheduler job that runs the automatic search-
query-details sync (app.services.search_query_details_sync_service) once a
day for every store that has Ozon Seller API credentials configured — the
same in-process-scheduler approach as
app.services.advertising_daily_scheduler, for the same reason (no external
task queue in this app; the scheduler starts/stops with the
`uvicorn app.main:app` process itself, no extra deployment step needed).

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
from app.core.encryption import decrypt_secret
from app.db.session import SessionLocal
from app.models.ozon_credentials import OzonCredentials
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.services.audit import record_audit
from app.services.ozon.client import OzonCredentials as OzonClientCredentials
from app.services.ozon.client import OzonSellerClient
from app.services.ozon.exceptions import OzonAPIError, OzonAuthError
from app.services.search_query_details_sync_service import sync_search_query_details

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_one_store(db: Session, creds: OzonCredentials) -> None:
    store_id = creds.store_id
    run = SyncRun(
        store_id=store_id,
        source_type=SyncSourceType.OZON_SEARCH_QUERY_STATISTICS_API,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    db.commit()

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)
    error_message = None
    try:
        with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
            outcome = sync_search_query_details(db, store_id=store_id, client=client)
        run.items_fetched = outcome.fetched
        run.items_created = outcome.created
        run.items_skipped_duplicate = outcome.updated
        error_message = "; ".join(outcome.errors[:20]) if outcome.errors else None
        run.status = SyncStatus.SUCCESS if not outcome.errors else (
            SyncStatus.PARTIAL if (outcome.created or outcome.updated) else SyncStatus.FAILED
        )
    except OzonAuthError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except OzonAPIError as exc:
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except Exception as exc:  # a SyncRun must never be left stuck "running" forever
        run.status = SyncStatus.FAILED
        error_message = f"Внутренняя ошибка: {exc}"
        logger.exception(
            "Позиции в поиске: непредвиденная ошибка планового автосбора статистики, store_id=%s", store_id
        )

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


def run_search_query_stats_for_all_stores() -> None:
    """The scheduled job body: one sync per store that has Ozon Seller API
    credentials, run sequentially — this endpoint's real per-account
    concurrency limits are not confirmed (see search_query_details_sync_
    service's own docstring), so sequential processing is the safe default,
    and it also keeps one crashed store from starving DB connections from
    the others."""
    db = SessionLocal()
    try:
        creds_list = (
            db.query(OzonCredentials)
            .filter(
                OzonCredentials.client_id_encrypted.isnot(None),
                OzonCredentials.api_key_encrypted.isnot(None),
            )
            .all()
        )
        logger.info(
            "Позиции в поиске: плановый автосбор статистики — магазинов с ключами Seller API: %d", len(creds_list)
        )
        for creds in creds_list:
            _run_one_store(db, creds)
    finally:
        db.close()


def start_search_query_stats_scheduler() -> BackgroundScheduler | None:
    settings = get_settings()
    if settings.ENV == "test" or not settings.SEARCH_QUERY_STATS_SCHEDULER_ENABLED:
        return None

    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_search_query_stats_for_all_stores,
        CronTrigger(hour=settings.SEARCH_QUERY_STATS_SCHEDULER_HOUR_UTC, minute=0),
        id="search_query_stats_sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info(
        "Позиции в поиске: планировщик автосбора статистики запущен (ежедневно в %02d:00 UTC)",
        settings.SEARCH_QUERY_STATS_SCHEDULER_HOUR_UTC,
    )
    return _scheduler


def stop_search_query_stats_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
