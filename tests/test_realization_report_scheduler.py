"""Tests for the in-process daily scheduler that tries to sync Ozon's
official monthly settlement report for every store with Ozon Seller API
credentials (app.services.realization_report_scheduler) — mirrors
test_order_daily_scheduler.py's own conventions."""
from app.core import config as config_module
from app.core.encryption import encrypt_secret
from app.models.ozon_credentials import OzonCredentials
from app.models.sync_run import SyncRun, SyncSourceType
from app.services.realization_report_sync_service import RealizationSyncOutcome


def test_scheduler_never_starts_under_env_test():
    import app.services.realization_report_scheduler as scheduler_module

    assert scheduler_module.start_realization_report_scheduler() is None


def test_scheduler_disabled_via_setting_is_a_noop(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("REALIZATION_REPORT_SCHEDULER_ENABLED", "false")
    config_module.get_settings.cache_clear()
    try:
        import app.services.realization_report_scheduler as scheduler_module

        assert scheduler_module.start_realization_report_scheduler() is None
    finally:
        config_module.get_settings.cache_clear()


def test_scheduler_registers_daily_job_at_the_configured_hour_and_stops_cleanly(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("REALIZATION_REPORT_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("REALIZATION_REPORT_SCHEDULER_HOUR_UTC", "6")
    monkeypatch.setenv("REALIZATION_REPORT_SCHEDULER_MINUTE_UTC", "30")
    config_module.get_settings.cache_clear()
    import app.services.realization_report_scheduler as scheduler_module

    try:
        scheduler = scheduler_module.start_realization_report_scheduler()
        assert scheduler is not None

        job = scheduler.get_job("realization_report_sync")
        assert job is not None
        hour_field = next(f for f in job.trigger.fields if f.name == "hour")
        minute_field = next(f for f in job.trigger.fields if f.name == "minute")
        assert str(hour_field) == "6"
        assert str(minute_field) == "30"

        assert scheduler_module.start_realization_report_scheduler() is scheduler
    finally:
        scheduler_module.stop_realization_report_scheduler()
        config_module.get_settings.cache_clear()


def test_run_one_store_records_a_sync_run_reflecting_the_outcome(db_session, two_stores_with_users, monkeypatch):
    import app.services.realization_report_scheduler as scheduler_module

    d = two_stores_with_users
    creds = OzonCredentials(
        store_id=d["store_a"].id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"),
    )
    db_session.add(creds)
    db_session.commit()

    monkeypatch.setattr(
        scheduler_module, "sync_missing_realization_reports",
        lambda db, *, store_id, client, backfill_months: [
            (2026, 8, RealizationSyncOutcome(fetched=True, created=True)),
            (2026, 9, RealizationSyncOutcome(not_yet_available=True, error="Report was not found")),
        ],
    )

    scheduler_module._run_one_store(db_session, creds, backfill_months=3)

    run = (
        db_session.query(SyncRun)
        .filter(SyncRun.store_id == d["store_a"].id, SyncRun.source_type == SyncSourceType.OZON_REALIZATION_REPORT_API)
        .one()
    )
    assert run.status.value == "success"
    assert run.items_created == 1
    assert "2026-09" in run.error_message
    assert "Ещё не готов" in run.error_message


def test_run_one_store_reports_partial_when_some_months_error(db_session, two_stores_with_users, monkeypatch):
    import app.services.realization_report_scheduler as scheduler_module

    d = two_stores_with_users
    creds = OzonCredentials(
        store_id=d["store_a"].id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"),
    )
    db_session.add(creds)
    db_session.commit()

    monkeypatch.setattr(
        scheduler_module, "sync_missing_realization_reports",
        lambda db, *, store_id, client, backfill_months: [
            (2026, 8, RealizationSyncOutcome(fetched=True, created=True)),
            (2026, 7, RealizationSyncOutcome(error="Ozon вернул ошибку сервера 500")),
        ],
    )

    scheduler_module._run_one_store(db_session, creds, backfill_months=3)

    run = (
        db_session.query(SyncRun)
        .filter(SyncRun.store_id == d["store_a"].id, SyncRun.source_type == SyncSourceType.OZON_REALIZATION_REPORT_API)
        .one()
    )
    assert run.status.value == "partial"
    assert "500" in run.error_message


def test_run_for_all_stores_only_processes_stores_with_seller_credentials(db_session, two_stores_with_users, monkeypatch):
    import app.services.realization_report_scheduler as scheduler_module

    d = two_stores_with_users
    db_session.add(
        OzonCredentials(store_id=d["store_a"].id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"))
    )
    db_session.commit()

    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        scheduler_module, "sync_missing_realization_reports",
        lambda db, *, store_id, client, backfill_months: [],
    )

    scheduler_module.run_realization_report_for_all_stores()

    runs = db_session.query(SyncRun).filter(SyncRun.source_type == SyncSourceType.OZON_REALIZATION_REPORT_API).all()
    assert len(runs) == 1
    assert runs[0].store_id == d["store_a"].id
