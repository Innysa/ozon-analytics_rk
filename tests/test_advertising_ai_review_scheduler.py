"""Tests for the in-process daily scheduler that generates the automatic
AI advertising-campaign review for every store with Ozon Performance API
credentials (app.services.advertising_ai_review_scheduler) — mirrors
test_search_query_stats_scheduler.py's structure for the analogous
search-query-stats scheduler."""
from app.core import config as config_module
from app.core.encryption import encrypt_secret
from app.models.ozon_credentials import OzonCredentials
from app.models.sync_run import SyncRun, SyncSourceType
from app.services.advertising_ai_review_service import AdvertisingAiReviewOutcome


def test_scheduler_never_starts_under_env_test():
    import app.services.advertising_ai_review_scheduler as scheduler_module

    assert scheduler_module.start_advertising_ai_review_scheduler() is None


def test_scheduler_disabled_via_setting_is_a_noop(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ADVERTISING_AI_REVIEW_SCHEDULER_ENABLED", "false")
    config_module.get_settings.cache_clear()
    try:
        import app.services.advertising_ai_review_scheduler as scheduler_module

        assert scheduler_module.start_advertising_ai_review_scheduler() is None
    finally:
        config_module.get_settings.cache_clear()


def test_scheduler_registers_daily_job_at_configured_hour_and_stops_cleanly(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ADVERTISING_AI_REVIEW_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("ADVERTISING_AI_REVIEW_SCHEDULER_HOUR_UTC", "5")
    config_module.get_settings.cache_clear()
    import app.services.advertising_ai_review_scheduler as scheduler_module

    try:
        scheduler = scheduler_module.start_advertising_ai_review_scheduler()
        assert scheduler is not None

        job = scheduler.get_job("advertising_ai_review")
        assert job is not None
        hour_field = next(f for f in job.trigger.fields if f.name == "hour")
        minute_field = next(f for f in job.trigger.fields if f.name == "minute")
        assert str(hour_field) == "5"
        assert str(minute_field) == "30"  # deliberately staggered past the on-the-hour jobs

        assert scheduler_module.start_advertising_ai_review_scheduler() is scheduler
    finally:
        scheduler_module.stop_advertising_ai_review_scheduler()
        config_module.get_settings.cache_clear()


def test_run_one_store_records_a_sync_run_reflecting_the_outcome(db_session, two_stores_with_users, monkeypatch):
    import app.services.advertising_ai_review_scheduler as scheduler_module

    d = two_stores_with_users
    monkeypatch.setattr(
        scheduler_module,
        "generate_advertising_ai_review",
        lambda db, *, store_id, ai_provider: AdvertisingAiReviewOutcome(campaigns_analyzed=3, saved=True, errors=[]),
    )

    scheduler_module._run_one_store(db_session, d["store_a"].id)

    run = (
        db_session.query(SyncRun)
        .filter(SyncRun.store_id == d["store_a"].id, SyncRun.source_type == SyncSourceType.OZON_ADVERTISING_AI_REVIEW)
        .one()
    )
    assert run.status.value == "success"
    assert run.items_fetched == 3
    assert run.items_created == 1
    assert run.error_message is None


def test_run_one_store_reports_failure_without_getting_stuck_running(db_session, two_stores_with_users, monkeypatch):
    import app.services.advertising_ai_review_scheduler as scheduler_module

    d = two_stores_with_users
    monkeypatch.setattr(
        scheduler_module,
        "generate_advertising_ai_review",
        lambda db, *, store_id, ai_provider: AdvertisingAiReviewOutcome(campaigns_analyzed=0, saved=False, errors=["нет данных"]),
    )

    scheduler_module._run_one_store(db_session, d["store_a"].id)

    run = (
        db_session.query(SyncRun)
        .filter(SyncRun.store_id == d["store_a"].id, SyncRun.source_type == SyncSourceType.OZON_ADVERTISING_AI_REVIEW)
        .one()
    )
    assert run.status.value == "failed"
    assert "нет данных" in run.error_message


def test_run_for_all_stores_only_processes_stores_with_performance_credentials(db_session, two_stores_with_users, monkeypatch):
    import app.services.advertising_ai_review_scheduler as scheduler_module

    d = two_stores_with_users
    db_session.add(
        OzonCredentials(
            store_id=d["store_a"].id,
            performance_client_id_encrypted=encrypt_secret("cid"),
            performance_client_secret_encrypted=encrypt_secret("secret"),
        )
    )
    db_session.add(OzonCredentials(store_id=d["store_b"].id))  # no Performance API creds
    db_session.commit()

    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    processed: list[str] = []
    monkeypatch.setattr(scheduler_module, "_run_one_store", lambda db, store_id: processed.append(store_id))

    scheduler_module.run_advertising_ai_review_for_all_stores()

    assert processed == [d["store_a"].id]
