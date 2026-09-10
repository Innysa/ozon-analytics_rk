"""Wires an in-process APScheduler job that runs the automatic cash-flow-
statement sync (app.services.cash_flow_statement_sync_service) once a day
for every store that has Ozon Seller API credentials configured. Same
in-process-BackgroundScheduler approach as every other scheduled job in
this app (see advertising_daily_scheduler's own docstring for why) — never
started under ENV=test.
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
from app.services.cash_flow_statement_sync_service import sync_cash_flow_statement_periods
from app.services.ozon.client import OzonCredentials as OzonClientCredentials
from app.services.ozon.client import OzonSellerClient
from app.services.ozon.exceptions import OzonAPIError, OzonAuthError

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_one_store(db: Session, creds: OzonCredentials) -> None:
    store_id = creds.store_id
    run = SyncRun(
        store_id=store_id,
        source_type=SyncSourceType.OZON_CASH_FLOW_STATEMENT_API,
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
            outcome = sync_cash_flow_statement_periods(db, store_id=store_id, client=client)
        run.items_fetched = outcome.fetched
        run.items_created = outcome.created
        run.items_skipped_duplicate = outcome.updated
        error_message = "; ".join(outcome.errors[:20]) if outcome.errors else None
        run.status = SyncStatus.SUCCESS if not outcome.errors else (
            SyncStatus.PARTIAL if (outcome.created or outcome.updated) else SyncStatus.FAILED
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
        logger.exception("ДДС: непредвиденная ошибка планового автосбора, store_id=%s", store_id)

    run.finished_at = datetime.now(timezone.utc)
    run.error_message = error_message
    record_audit(
        db, action="sync_finished", store_id=store_id, target_type="sync_run", target_id=run.id,
        result="success" if run.status == SyncStatus.SUCCESS else "failure", message=error_message,
    )
    db.commit()


def run_cash_flow_statement_for_all_stores() -> None:
    db = SessionLocal()
    try:
        creds_list = (
            db.query(OzonCredentials)
            .filter(OzonCredentials.client_id_encrypted.isnot(None), OzonCredentials.api_key_encrypted.isnot(None))
            .all()
        )
        logger.info("ДДС: плановый автосбор — магазинов с ключами Ozon Seller API: %d", len(creds_list))
        for creds in creds_list:
            _run_one_store(db, creds)
    finally:
        db.close()


def start_cash_flow_statement_scheduler() -> BackgroundScheduler | None:
    settings = get_settings()
    if settings.ENV == "test" or not settings.CASH_FLOW_STATEMENT_SCHEDULER_ENABLED:
        return None

    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_cash_flow_statement_for_all_stores,
        CronTrigger(hour=settings.CASH_FLOW_STATEMENT_SCHEDULER_HOUR_UTC, minute=settings.CASH_FLOW_STATEMENT_SCHEDULER_MINUTE_UTC),
        id="cash_flow_statement_sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info(
        "ДДС: планировщик автосбора запущен (ежедневно в %02d:%02d UTC)",
        settings.CASH_FLOW_STATEMENT_SCHEDULER_HOUR_UTC, settings.CASH_FLOW_STATEMENT_SCHEDULER_MINUTE_UTC,
    )
    return _scheduler


def stop_cash_flow_statement_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
