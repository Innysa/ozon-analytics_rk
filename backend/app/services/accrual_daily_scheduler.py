"""Wires an in-process APScheduler job that re-syncs Ozon's true daily
accrual total (app.services.accrual_daily_sync_service) once a day for
every store with Ozon Seller API credentials configured — see that
service's own docstring for why a short TRAILING window of days is
re-fetched every night, not just "yesterday" once. Same in-process-
BackgroundScheduler approach as every other scheduled job in this app
(see advertising_daily_scheduler's own docstring for why) — never started
under ENV=test.
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
from app.services.accrual_daily_sync_service import sync_recent_accrual_days
from app.services.audit import record_audit
from app.services.ozon.client import OzonCredentials as OzonClientCredentials
from app.services.ozon.client import OzonSellerClient
from app.services.ozon.exceptions import OzonAPIError, OzonAuthError

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_one_store(db: Session, creds: OzonCredentials, *, days: int) -> None:
    store_id = creds.store_id
    run = SyncRun(
        store_id=store_id,
        source_type=SyncSourceType.OZON_ACCRUAL_DAILY_API,
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
            results = sync_recent_accrual_days(db, store_id=store_id, client=client, days=days)

        fetched = sum(1 for _, o in results if o.fetched)
        created = sum(1 for _, o in results if o.fetched and o.created)
        updated = fetched - created
        real_errors = [f"{d.isoformat()}: {o.error}" for d, o in results if o.error]

        run.items_fetched = fetched
        run.items_created = created
        run.items_skipped_duplicate = updated
        error_message = "; ".join(real_errors[:20]) if real_errors else None
        run.status = SyncStatus.SUCCESS if not real_errors else (
            SyncStatus.PARTIAL if fetched else SyncStatus.FAILED
        )
    except OzonAuthError as exc:
        db.rollback()
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except OzonAPIError as exc:
        db.rollback()
        run.status = SyncStatus.FAILED
        error_message = str(exc)
    except Exception as exc:  # a SyncRun must never be left stuck "running" forever
        db.rollback()
        run.status = SyncStatus.FAILED
        error_message = f"Внутренняя ошибка: {exc}"
        logger.exception("Начисления по дням: непредвиденная ошибка планового автосбора, store_id=%s", store_id)

    run.finished_at = datetime.now(timezone.utc)
    run.error_message = error_message
    record_audit(
        db, action="sync_finished", store_id=store_id, target_type="sync_run", target_id=run.id,
        result="success" if run.status == SyncStatus.SUCCESS else "failure", message=error_message,
    )
    db.commit()


def run_accrual_daily_for_all_stores() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        creds_list = (
            db.query(OzonCredentials)
            .filter(OzonCredentials.client_id_encrypted.isnot(None), OzonCredentials.api_key_encrypted.isnot(None))
            .all()
        )
        logger.info("Начисления по дням: плановый автосбор — магазинов с ключами Ozon Seller API: %d", len(creds_list))
        for creds in creds_list:
            _run_one_store(db, creds, days=settings.ACCRUAL_DAILY_TRAILING_DAYS)
    finally:
        db.close()


def start_accrual_daily_scheduler() -> BackgroundScheduler | None:
    settings = get_settings()
    if settings.ENV == "test" or not settings.ACCRUAL_DAILY_SCHEDULER_ENABLED:
        return None

    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_accrual_daily_for_all_stores,
        CronTrigger(hour=settings.ACCRUAL_DAILY_SCHEDULER_HOUR_UTC, minute=settings.ACCRUAL_DAILY_SCHEDULER_MINUTE_UTC),
        id="accrual_daily_sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info(
        "Начисления по дням: планировщик автосбора запущен (ежедневно в %02d:%02d UTC)",
        settings.ACCRUAL_DAILY_SCHEDULER_HOUR_UTC, settings.ACCRUAL_DAILY_SCHEDULER_MINUTE_UTC,
    )
    return _scheduler


def stop_accrual_daily_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
