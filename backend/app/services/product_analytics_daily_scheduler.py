"""Wires in-process APScheduler jobs that run the automatic per-product
funnel sync (app.services.product_analytics_daily_sync_service) SEVERAL
times overnight (not once) for every store that has Ozon Seller API
credentials configured. Same in-process-BackgroundScheduler approach as
every other scheduled job in this app (see advertising_daily_scheduler's
own docstring for why) — never started under ENV=test.

**ИЗМЕНЕНО 2026-09-24**: was a single daily run at 03:45 UTC (06:45 МСК).
The user reported "Заказано" on «РНП» was STILL wrong the evening after
that single run, and asked explicitly: "сделай значить чтобы раза 2-3
обращался к озон запрос для верных данных... надо чтобы примерно в 4 по
мск все данные были подтянуты за вчерашний день." Two real problems with
the old single-shot schedule: (1) 06:45 МСК is itself past her ~4:00 МСК
deadline, and (2) being a single attempt, it had no way to recover if that
one run hit a transient failure OR ran before Ozon had actually finished
computing the previous day's funnel numbers (Ozon does NOT document when
in the day this data finalizes — UNCONFIRMED). PRODUCT_ANALYTICS_STATS_
SCHEDULER_TIMES_UTC (see core.config) now lists several "HH:MM" UTC times,
each becoming its own APScheduler job calling the SAME sync function with
the SAME rolling lookback window — later attempts simply upsert over
earlier ones (idempotent, see sync_product_analytics_daily_statistics), so
a more-complete later run naturally supersedes an earlier partial one. All
default times land before ~04:00 МСК.

Note: unlike order_daily_scheduler, this sync requires Ozon Premium Plus/
Premium Pro — a store without it will simply get a failed SyncRun each run
(whatever error Ozon actually returns), not a special-cased skip, since
there is no confirmed way to detect the subscription tier up front without
calling the method itself.
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
from app.services.product_analytics_daily_sync_service import sync_product_analytics_daily_statistics

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_one_store(db: Session, creds: OzonCredentials) -> None:
    store_id = creds.store_id
    run = SyncRun(
        store_id=store_id,
        source_type=SyncSourceType.OZON_ANALYTICS_DATA_API,
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
            outcome = sync_product_analytics_daily_statistics(db, store_id=store_id, client=client)
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
        logger.exception("Аналитика товаров: непредвиденная ошибка планового автосбора, store_id=%s", store_id)

    run.finished_at = datetime.now(timezone.utc)
    run.error_message = error_message
    record_audit(
        db, action="sync_finished", store_id=store_id, target_type="sync_run", target_id=run.id,
        result="success" if run.status == SyncStatus.SUCCESS else "failure", message=error_message,
    )
    db.commit()


def run_product_analytics_daily_statistics_for_all_stores() -> None:
    db = SessionLocal()
    try:
        creds_list = (
            db.query(OzonCredentials)
            .filter(OzonCredentials.client_id_encrypted.isnot(None), OzonCredentials.api_key_encrypted.isnot(None))
            .all()
        )
        logger.info("Аналитика товаров: плановый автосбор — магазинов с ключами Ozon Seller API: %d", len(creds_list))
        for creds in creds_list:
            _run_one_store(db, creds)
    finally:
        db.close()


def _parse_scheduler_times(raw: str) -> list[tuple[int, int]]:
    """Parses "HH:MM,HH:MM,..." (UTC) into (hour, minute) tuples. A blank or
    malformed entry is skipped rather than crashing app startup over a typo
    in an env var — same defensive posture as _blank_session_cookie_secure_
    means_unset in core.config."""
    times: list[tuple[int, int]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        hour_str, _, minute_str = chunk.partition(":")
        try:
            times.append((int(hour_str), int(minute_str)))
        except ValueError:
            logger.warning("Аналитика товаров: не удалось разобрать время планировщика %r — пропущено", chunk)
    return times


def start_product_analytics_daily_statistics_scheduler() -> BackgroundScheduler | None:
    settings = get_settings()
    if settings.ENV == "test" or not settings.PRODUCT_ANALYTICS_STATS_SCHEDULER_ENABLED:
        return None

    global _scheduler
    if _scheduler is not None:
        return _scheduler

    times = _parse_scheduler_times(settings.PRODUCT_ANALYTICS_STATS_SCHEDULER_TIMES_UTC)
    if not times:
        logger.warning("Аналитика товаров: PRODUCT_ANALYTICS_STATS_SCHEDULER_TIMES_UTC пуст/некорректен — планировщик не запущен")
        return None

    _scheduler = BackgroundScheduler(timezone="UTC")
    for i, (hour, minute) in enumerate(times):
        _scheduler.add_job(
            run_product_analytics_daily_statistics_for_all_stores,
            CronTrigger(hour=hour, minute=minute),
            id=f"product_analytics_daily_statistics_sync_{i}",
            replace_existing=True,
            misfire_grace_time=3600,
        )
    _scheduler.start()
    logger.info(
        "Аналитика товаров: планировщик автосбора запущен (%d попыток/сутки, UTC: %s)",
        len(times), ", ".join(f"{h:02d}:{m:02d}" for h, m in times),
    )
    return _scheduler


def stop_product_analytics_daily_statistics_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
