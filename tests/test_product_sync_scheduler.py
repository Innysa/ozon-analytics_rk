"""Tests for the nightly product-sync scheduler (app.services.scheduler).

Two things matter here that a route-level test can't cover: (1) the job must
only pick up stores that actually have Seller API credentials configured,
never guessing/fabricating a run for stores without them, and (2) one
store's sync blowing up must not stop the rest of the batch — otherwise a
single bad store would silently starve every other store of its nightly
refresh."""
from unittest.mock import MagicMock

from apscheduler.triggers.cron import CronTrigger

import app.services.scheduler as scheduler_module
from app.core.config import get_settings
from app.models.ozon_credentials import OzonCredentials


def _seed_credentials(db_session, store_id: str, *, with_seller_keys: bool) -> OzonCredentials:
    creds = OzonCredentials(
        store_id=store_id,
        client_id_encrypted="enc-cid" if with_seller_keys else None,
        api_key_encrypted="enc-key" if with_seller_keys else None,
    )
    db_session.add(creds)
    db_session.commit()
    return creds


def test_start_scheduler_is_disabled_under_test_env():
    """conftest.py sets ENV=test for the whole suite — the scheduler must
    never actually start a background thread while tests are running."""
    get_settings.cache_clear()
    try:
        assert scheduler_module.start_scheduler() is None
    finally:
        get_settings.cache_clear()


def test_nightly_sync_only_targets_stores_with_seller_credentials(db_session, two_stores_with_users, monkeypatch):
    d = two_stores_with_users
    _seed_credentials(db_session, d["store_a"].id, with_seller_keys=True)
    _seed_credentials(db_session, d["store_b"].id, with_seller_keys=False)  # e.g. Performance-only creds

    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)  # conftest owns closing this session

    synced_store_ids = []
    monkeypatch.setattr(
        scheduler_module,
        "sync_store_products",
        lambda db, store_id, creds, *, initiated_by_user_id: synced_store_ids.append(store_id),
    )

    scheduler_module.run_nightly_product_sync()

    assert synced_store_ids == [d["store_a"].id]


def test_nightly_sync_continues_after_one_store_fails(db_session, two_stores_with_users, monkeypatch):
    d = two_stores_with_users
    _seed_credentials(db_session, d["store_a"].id, with_seller_keys=True)
    _seed_credentials(db_session, d["store_b"].id, with_seller_keys=True)

    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(db_session, "rollback", lambda: None)

    synced_store_ids = []

    def _fake_sync(db, store_id, creds, *, initiated_by_user_id):
        if store_id == d["store_a"].id:
            raise RuntimeError("Ozon недоступен")
        synced_store_ids.append(store_id)

    monkeypatch.setattr(scheduler_module, "sync_store_products", _fake_sync)

    scheduler_module.run_nightly_product_sync()  # must not raise

    assert synced_store_ids == [d["store_b"].id]


def test_start_scheduler_registers_configured_cron_time(monkeypatch):
    monkeypatch.setattr(scheduler_module, "_scheduler", None)
    settings = get_settings()
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "PRODUCT_SYNC_ENABLED", True)
    monkeypatch.setattr(settings, "PRODUCT_SYNC_HOUR_UTC", 2)
    monkeypatch.setattr(settings, "PRODUCT_SYNC_MINUTE_UTC", 30)
    monkeypatch.setattr(scheduler_module, "get_settings", lambda: settings)

    fake_scheduler = MagicMock()
    monkeypatch.setattr(scheduler_module, "BackgroundScheduler", MagicMock(return_value=fake_scheduler))

    try:
        result = scheduler_module.start_scheduler()
        assert result is fake_scheduler
        fake_scheduler.start.assert_called_once()
        _, kwargs = fake_scheduler.add_job.call_args
        assert kwargs["id"] == scheduler_module.JOB_ID
        trigger = kwargs["trigger"]
        assert isinstance(trigger, CronTrigger)
        fields_by_name = {f.name: str(f) for f in trigger.fields}
        assert fields_by_name["hour"] == "2"
        assert fields_by_name["minute"] == "30"
    finally:
        scheduler_module._scheduler = None
