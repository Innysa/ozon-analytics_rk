"""Tests for the in-process daily scheduler that runs the automatic orders
sync for every store with Ozon Seller API credentials
(app.services.order_daily_scheduler) — mirrors
test_search_query_stats_scheduler.py's own conventions."""
from app.core import config as config_module
from app.core.encryption import encrypt_secret
from app.models.ozon_credentials import OzonCredentials
from app.models.sync_run import SyncRun, SyncSourceType
from app.services.order_daily_sync_service import SyncOutcome


def test_scheduler_never_starts_under_env_test():
    import app.services.order_daily_scheduler as scheduler_module

    assert scheduler_module.start_order_daily_statistics_scheduler() is None


def test_scheduler_disabled_via_setting_is_a_noop(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ORDER_STATS_SCHEDULER_ENABLED", "false")
    config_module.get_settings.cache_clear()
    try:
        import app.services.order_daily_scheduler as scheduler_module

        assert scheduler_module.start_order_daily_statistics_scheduler() is None
    finally:
        config_module.get_settings.cache_clear()


def test_scheduler_registers_daily_job_at_the_configured_hour_and_stops_cleanly(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ORDER_STATS_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("ORDER_STATS_SCHEDULER_HOUR_UTC", "5")
    monkeypatch.setenv("ORDER_STATS_SCHEDULER_MINUTE_UTC", "45")
    config_module.get_settings.cache_clear()
    import app.services.order_daily_scheduler as scheduler_module

    try:
        scheduler = scheduler_module.start_order_daily_statistics_scheduler()
        assert scheduler is not None

        job = scheduler.get_job("order_daily_statistics_sync")
        assert job is not None
        hour_field = next(f for f in job.trigger.fields if f.name == "hour")
        minute_field = next(f for f in job.trigger.fields if f.name == "minute")
        assert str(hour_field) == "5"
        assert str(minute_field) == "45"

        assert scheduler_module.start_order_daily_statistics_scheduler() is scheduler
    finally:
        scheduler_module.stop_order_daily_statistics_scheduler()
        config_module.get_settings.cache_clear()


def test_run_one_store_records_a_sync_run_reflecting_the_outcome(db_session, two_stores_with_users, monkeypatch):
    import app.services.order_daily_scheduler as scheduler_module

    d = two_stores_with_users
    creds = OzonCredentials(
        store_id=d["store_a"].id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"),
    )
    db_session.add(creds)
    db_session.commit()

    monkeypatch.setattr(
        scheduler_module, "sync_order_daily_statistics",
        lambda db, *, store_id, client: SyncOutcome(fetched=5, created=5, updated=0, errors=[]),
    )

    scheduler_module._run_one_store(db_session, creds)

    run = (
        db_session.query(SyncRun)
        .filter(SyncRun.store_id == d["store_a"].id, SyncRun.source_type == SyncSourceType.OZON_ORDERS_API)
        .one()
    )
    assert run.status.value == "success"
    assert run.items_created == 5
    assert run.error_message is None


def test_run_for_all_stores_only_processes_stores_with_seller_credentials(db_session, two_stores_with_users, monkeypatch):
    import app.services.order_daily_scheduler as scheduler_module

    d = two_stores_with_users
    db_session.add(
        OzonCredentials(store_id=d["store_a"].id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"))
    )
    db_session.add(OzonCredentials(store_id=d["store_b"].id))  # no Seller API creds
    db_session.commit()

    # Reuse this test's own rollback-safe session instead of a real second
    # SessionLocal() connection, with close() disarmed so the scheduler's
    # own `finally: db.close()` doesn't interfere with db_session's fixture
    # teardown.
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    processed: list[str] = []
    monkeypatch.setattr(scheduler_module, "_run_one_store", lambda db, creds: processed.append(creds.store_id))

    scheduler_module.run_order_daily_statistics_for_all_stores()

    assert processed == [d["store_a"].id]
