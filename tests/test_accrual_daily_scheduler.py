"""Tests for the in-process nightly scheduler that re-syncs Ozon's true
daily accrual total for every store with Ozon Seller API credentials
(app.services.accrual_daily_scheduler) — mirrors
test_realization_report_scheduler.py's own conventions."""
from app.core import config as config_module
from app.core.encryption import encrypt_secret
from app.models.ozon_credentials import OzonCredentials
from app.models.sync_run import SyncRun, SyncSourceType
from app.services.accrual_daily_sync_service import AccrualSyncOutcome


def test_scheduler_never_starts_under_env_test():
    import app.services.accrual_daily_scheduler as scheduler_module

    assert scheduler_module.start_accrual_daily_scheduler() is None


def test_scheduler_disabled_via_setting_is_a_noop(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ACCRUAL_DAILY_SCHEDULER_ENABLED", "false")
    config_module.get_settings.cache_clear()
    try:
        import app.services.accrual_daily_scheduler as scheduler_module

        assert scheduler_module.start_accrual_daily_scheduler() is None
    finally:
        config_module.get_settings.cache_clear()


def test_scheduler_registers_daily_job_at_the_configured_hour_and_stops_cleanly(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ACCRUAL_DAILY_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("ACCRUAL_DAILY_SCHEDULER_HOUR_UTC", "4")
    monkeypatch.setenv("ACCRUAL_DAILY_SCHEDULER_MINUTE_UTC", "15")
    config_module.get_settings.cache_clear()
    import app.services.accrual_daily_scheduler as scheduler_module

    try:
        scheduler = scheduler_module.start_accrual_daily_scheduler()
        assert scheduler is not None

        job = scheduler.get_job("accrual_daily_sync")
        assert job is not None
        hour_field = next(f for f in job.trigger.fields if f.name == "hour")
        minute_field = next(f for f in job.trigger.fields if f.name == "minute")
        assert str(hour_field) == "4"
        assert str(minute_field) == "15"

        assert scheduler_module.start_accrual_daily_scheduler() is scheduler
    finally:
        scheduler_module.stop_accrual_daily_scheduler()
        config_module.get_settings.cache_clear()


def test_run_one_store_records_a_sync_run_reflecting_the_outcome(db_session, two_stores_with_users, monkeypatch):
    import app.services.accrual_daily_scheduler as scheduler_module
    from datetime import date

    d = two_stores_with_users
    creds = OzonCredentials(
        store_id=d["store_a"].id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"),
    )
    db_session.add(creds)
    db_session.commit()

    monkeypatch.setattr(
        scheduler_module, "sync_recent_accrual_days",
        lambda db, *, store_id, client, days: [
            (date(2026, 9, 13), AccrualSyncOutcome(fetched=True, created=True)),
            (date(2026, 9, 12), AccrualSyncOutcome(fetched=True, created=False)),
        ],
    )

    scheduler_module._run_one_store(db_session, creds, days=2)

    run = (
        db_session.query(SyncRun)
        .filter(SyncRun.store_id == d["store_a"].id, SyncRun.source_type == SyncSourceType.OZON_ACCRUAL_DAILY_API)
        .one()
    )
    assert run.status.value == "success"
    assert run.items_created == 1
    assert run.items_skipped_duplicate == 1


def test_run_one_store_reports_partial_when_some_days_error(db_session, two_stores_with_users, monkeypatch):
    import app.services.accrual_daily_scheduler as scheduler_module
    from datetime import date

    d = two_stores_with_users
    creds = OzonCredentials(
        store_id=d["store_a"].id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"),
    )
    db_session.add(creds)
    db_session.commit()

    monkeypatch.setattr(
        scheduler_module, "sync_recent_accrual_days",
        lambda db, *, store_id, client, days: [
            (date(2026, 9, 13), AccrualSyncOutcome(fetched=True, created=True)),
            (date(2026, 9, 12), AccrualSyncOutcome(error="Ozon вернул ошибку сервера 500")),
        ],
    )

    scheduler_module._run_one_store(db_session, creds, days=2)

    run = (
        db_session.query(SyncRun)
        .filter(SyncRun.store_id == d["store_a"].id, SyncRun.source_type == SyncSourceType.OZON_ACCRUAL_DAILY_API)
        .one()
    )
    assert run.status.value == "partial"
    assert "500" in run.error_message


def test_run_for_all_stores_only_processes_stores_with_seller_credentials(db_session, two_stores_with_users, monkeypatch):
    import app.services.accrual_daily_scheduler as scheduler_module

    d = two_stores_with_users
    db_session.add(
        OzonCredentials(store_id=d["store_a"].id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"))
    )
    db_session.commit()

    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        scheduler_module, "sync_recent_accrual_days",
        lambda db, *, store_id, client, days: [],
    )

    scheduler_module.run_accrual_daily_for_all_stores()

    runs = db_session.query(SyncRun).filter(SyncRun.source_type == SyncSourceType.OZON_ACCRUAL_DAILY_API).all()
    assert len(runs) == 1
    assert runs[0].store_id == d["store_a"].id
