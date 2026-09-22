"""Wires in-process APScheduler jobs that run the automatic orders sync
(app.services.order_daily_sync_service) for every store that has Ozon
Seller API credentials configured. Same in-process-BackgroundScheduler
approach as every other scheduled job in this app (see
advertising_daily_scheduler's own docstring for why) — never started under
ENV=test.

TWO jobs, not one, as of 2026-09-22:

  1. The original FULL nightly sync (order_daily_statistics_sync) — the
     entire ORDER_STATS_DEFAULT_LOOKBACK_DAYS window, once a day, off-peak.
     This is the safety net that eventually catches every day, however long
     Ozon takes to stamp in_process_at (see order_daily_sync_service's own
     docstring — that field lags behind the real order-placement moment,
     sometimes by more than a day).
  2. A NEW frequent, NARROW sync (order_daily_statistics_recent_sync) — just
     the last ORDER_STATS_RECENT_SYNC_LOOKBACK_DAYS days, every
     ORDER_STATS_RECENT_SYNC_INTERVAL_HOURS hours. Added because the owner
     (fairly) pushed back on "check back tomorrow, or click the button
     yourself" as an answer to today's/yesterday's counts looking low
     against Ozon's own cabinet — she wants everything to catch up on its
     own, same as every other sync in this app already does. A few-day
     window is a handful of requests (one chunk each for FBO/FBS at the
     default ORDER_STATS_SYNC_CHUNK_DAYS=3), so running it every couple of
     hours doesn't reproduce the sustained-429 problem the once-daily FULL
     sync's own chunking/backoff was built to survive (see
     sync_order_daily_statistics's own docstring) — it's a small fraction of
     that job's request volume, run more often specifically because it's
     cheap. Both jobs share find_blocking_running_sync()'s same race guard
     (keyed by store_id + source_type only, not by window size), so a
     recent-sync tick landing while the full nightly sync is still mid-run
     simply skips that store this tick, same as two overlapping full runs
     already did before this existed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.encryption import decrypt_secret
from app.db.session import SessionLocal
from app.models.ozon_credentials import OzonCredentials
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.services.audit import record_audit
from app.services.order_daily_sync_service import (
    commission_missing_units_note,
    fetched_by_schema_note,
    find_blocking_running_sync,
    skipped_no_process_date_note,
    sync_order_daily_statistics,
    truncated_pagination_note,
)
from app.services.ozon.client import OzonCredentials as OzonClientCredentials
from app.services.ozon.client import OzonSellerClient
from app.services.ozon.exceptions import OzonAPIError, OzonAuthError

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_one_store(db: Session, creds: OzonCredentials, *, date_from=None, date_to=None) -> None:
    store_id = creds.store_id
    if find_blocking_running_sync(db, store_id=store_id):
        # A manual "Обновить заказы (авто)" run (or a still-finishing earlier
        # scheduled run — full OR recent, both share this guard) is already
        # in flight for this store — see find_blocking_running_sync()'s own
        # docstring for the real race this guards against. Skip this store
        # this tick rather than risk two overlapping runs clobbering each
        # other's writes; the next tick (recent sync, every few hours, or
        # the full nightly sync) picks it up as usual.
        logger.info("Заказы: пропускаю плановый автосбор для store_id=%s — синхронизация уже выполняется", store_id)
        return

    run = SyncRun(
        store_id=store_id,
        source_type=SyncSourceType.OZON_ORDERS_API,
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
        window_kwargs = {}
        if date_from is not None:
            window_kwargs["date_from"] = date_from
        if date_to is not None:
            window_kwargs["date_to"] = date_to
        with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
            outcome = sync_order_daily_statistics(db, store_id=store_id, client=client, **window_kwargs)
        run.items_fetched = outcome.fetched
        run.items_created = outcome.created
        run.items_skipped_duplicate = outcome.updated
        notes = list(outcome.errors[:20])
        skipped_note = skipped_no_process_date_note(outcome)
        if skipped_note:
            notes.append(skipped_note)
        # commission_note/schema_note were missing from THIS (scheduled)
        # path until 2026-09-12 — they'd been wired into the manual-trigger
        # route (app.api.routes.sync) only, so a store relying solely on the
        # daily automatic sync never saw either note even when relevant.
        commission_note = commission_missing_units_note(outcome)
        if commission_note:
            notes.append(commission_note)
        schema_note = fetched_by_schema_note(outcome)
        if schema_note:
            notes.append(schema_note)
        truncation_note = truncated_pagination_note(outcome)
        if truncation_note:
            notes.append(truncation_note)
        if date_from is not None or date_to is not None:
            notes.append(f"Быстрое обновление последних дней ({date_from}—{date_to})")
        error_message = "; ".join(notes) if notes else None
        has_data_loss_risk = bool(outcome.errors) or bool(outcome.truncated_chunks)
        run.status = SyncStatus.SUCCESS if not has_data_loss_risk else (
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
        logger.exception("Заказы: непредвиденная ошибка планового автосбора, store_id=%s", store_id)

    run.finished_at = datetime.now(timezone.utc)
    run.error_message = error_message
    record_audit(
        db, action="sync_finished", store_id=store_id, target_type="sync_run", target_id=run.id,
        result="success" if run.status == SyncStatus.SUCCESS else "failure", message=error_message,
    )
    db.commit()


def run_order_daily_statistics_for_all_stores() -> None:
    db = SessionLocal()
    try:
        creds_list = (
            db.query(OzonCredentials)
            .filter(OzonCredentials.client_id_encrypted.isnot(None), OzonCredentials.api_key_encrypted.isnot(None))
            .all()
        )
        logger.info("Заказы: плановый автосбор — магазинов с ключами Ozon Seller API: %d", len(creds_list))
        for creds in creds_list:
            _run_one_store(db, creds)
    finally:
        db.close()


def run_recent_order_daily_statistics_for_all_stores() -> None:
    """Same store loop as the full sync above, but narrowed to just the last
    ORDER_STATS_RECENT_SYNC_LOOKBACK_DAYS days — see this module's own
    docstring for why this exists as a SEPARATE, more frequent job rather
    than just shortening the one daily sync."""
    settings = get_settings()
    db = SessionLocal()
    try:
        creds_list = (
            db.query(OzonCredentials)
            .filter(OzonCredentials.client_id_encrypted.isnot(None), OzonCredentials.api_key_encrypted.isnot(None))
            .all()
        )
        today = datetime.now(timezone.utc).date()
        date_from = today - timedelta(days=settings.ORDER_STATS_RECENT_SYNC_LOOKBACK_DAYS - 1)
        logger.info(
            "Заказы: частый автосбор последних дней (%s—%s) — магазинов с ключами Ozon Seller API: %d",
            date_from, today, len(creds_list),
        )
        for creds in creds_list:
            _run_one_store(db, creds, date_from=date_from, date_to=today)
    finally:
        db.close()


def start_order_daily_statistics_scheduler() -> BackgroundScheduler | None:
    settings = get_settings()
    if settings.ENV == "test" or not settings.ORDER_STATS_SCHEDULER_ENABLED:
        return None

    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_order_daily_statistics_for_all_stores,
        CronTrigger(hour=settings.ORDER_STATS_SCHEDULER_HOUR_UTC, minute=settings.ORDER_STATS_SCHEDULER_MINUTE_UTC),
        id="order_daily_statistics_sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info(
        "Заказы: планировщик автосбора запущен (полный, ежедневно в %02d:%02d UTC)",
        settings.ORDER_STATS_SCHEDULER_HOUR_UTC, settings.ORDER_STATS_SCHEDULER_MINUTE_UTC,
    )
    if settings.ORDER_STATS_RECENT_SYNC_ENABLED:
        _scheduler.add_job(
            run_recent_order_daily_statistics_for_all_stores,
            IntervalTrigger(hours=settings.ORDER_STATS_RECENT_SYNC_INTERVAL_HOURS),
            id="order_daily_statistics_recent_sync",
            replace_existing=True,
            misfire_grace_time=1800,
        )
        logger.info(
            "Заказы: планировщик автосбора запущен (последние %d дн., каждые %d ч.)",
            settings.ORDER_STATS_RECENT_SYNC_LOOKBACK_DAYS, settings.ORDER_STATS_RECENT_SYNC_INTERVAL_HOURS,
        )
    _scheduler.start()
    return _scheduler


def stop_order_daily_statistics_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
