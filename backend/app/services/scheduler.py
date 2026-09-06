"""In-process nightly scheduler for the Ozon product/stock sync.

Runs inside the same single uvicorn process as the API (see app.main and
docker/entrypoint.sh — this deployment never runs more than one app
process), so an in-app job is simpler and more portable than a host cron
job or a second container: no extra process to supervise, and no need to
fake an authenticated HTTP call into the API from outside it (the app has
no service-to-service API token, only cookie sessions). The manual
"Синхронизировать с Ozon" button keeps working independently of this —
both call the same app.services.product_sync.sync_store_products.

APScheduler's BackgroundScheduler runs the job on its own thread, so it
doesn't interfere with the asyncio event loop FastAPI/uvicorn use for
requests; each run opens its own short-lived DB session per store rather
than reusing one across the whole batch, and one store's failure is caught
and logged so it never stops the rest from syncing.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.ozon_credentials import OzonCredentials
from app.services.product_sync import sync_store_products

logger = logging.getLogger(__name__)

JOB_ID = "ozon_products_nightly_sync"

_scheduler: BackgroundScheduler | None = None


def run_nightly_product_sync() -> None:
    """Syncs products/stocks for every store that has Seller API credentials
    configured. Called by the scheduled job; also safe to call directly
    (e.g. from a one-off script) since it owns its own DB session."""
    db = SessionLocal()
    try:
        stores_with_creds = (
            db.query(OzonCredentials)
            .filter(OzonCredentials.client_id_encrypted.isnot(None), OzonCredentials.api_key_encrypted.isnot(None))
            .all()
        )
        logger.info("Ночная синхронизация товаров с Ozon: магазинов с ключами — %d", len(stores_with_creds))
        for creds in stores_with_creds:
            try:
                sync_store_products(db, creds.store_id, creds, initiated_by_user_id=None)
            except Exception:
                db.rollback()
                logger.exception("Ночная синхронизация товаров с Ozon упала для магазина %s", creds.store_id)
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler | None:
    """Starts the nightly job unless already running or disabled. Safe to
    call more than once (e.g. once per TestClient in tests) — a no-op after
    the first successful start."""
    global _scheduler
    settings = get_settings()

    if not settings.PRODUCT_SYNC_ENABLED or settings.ENV == "test":
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_nightly_product_sync,
        trigger=CronTrigger(hour=settings.PRODUCT_SYNC_HOUR_UTC, minute=settings.PRODUCT_SYNC_MINUTE_UTC),
        id=JOB_ID,
        replace_existing=True,
        coalesce=True,  # a missed run (e.g. container was down) doesn't queue up repeats on restart
        max_instances=1,  # never run two syncs of the same store set concurrently
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Ночная синхронизация товаров с Ozon запланирована на %02d:%02d UTC",
        settings.PRODUCT_SYNC_HOUR_UTC,
        settings.PRODUCT_SYNC_MINUTE_UTC,
    )
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
