"""Tests for the in-process daily scheduler that runs the automatic product
catalog sync (price/stock/ProductPriceDailySnapshot) for every store with
Ozon Seller API credentials (app.services.product_catalog_scheduler) —
mirrors test_product_analytics_daily_scheduler.py's own conventions."""
from app.core import config as config_module
from app.core.encryption import encrypt_secret
from app.models.ozon_credentials import OzonCredentials
from app.models.sync_run import SyncRun, SyncSourceType
from app.services.product_catalog_sync_service import ProductCatalogSyncOutcome


def test_scheduler_never_starts_under_env_test():
    import app.services.product_catalog_scheduler as scheduler_module

    assert scheduler_module.start_product_catalog_scheduler() is None


def test_scheduler_disabled_via_setting_is_a_noop(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("PRODUCT_CATALOG_SCHEDULER_ENABLED", "false")
    config_module.get_settings.cache_clear()
    try:
        import app.services.product_catalog_scheduler as scheduler_module

        assert scheduler_module.start_product_catalog_scheduler() is None
    finally:
        config_module.get_settings.cache_clear()


def test_scheduler_registers_daily_job_at_the_configured_hour_and_stops_cleanly(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("PRODUCT_CATALOG_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("PRODUCT_CATALOG_SCHEDULER_HOUR_UTC", "23")
    monkeypatch.setenv("PRODUCT_CATALOG_SCHEDULER_MINUTE_UTC", "30")
    config_module.get_settings.cache_clear()
    import app.services.product_catalog_scheduler as scheduler_module

    try:
        scheduler = scheduler_module.start_product_catalog_scheduler()
        assert scheduler is not None

        job = scheduler.get_job("product_catalog_sync")
        assert job is not None
        hour_field = next(f for f in job.trigger.fields if f.name == "hour")
        minute_field = next(f for f in job.trigger.fields if f.name == "minute")
        assert str(hour_field) == "23"
        assert str(minute_field) == "30"

        assert scheduler_module.start_product_catalog_scheduler() is scheduler
    finally:
        scheduler_module.stop_product_catalog_scheduler()
        config_module.get_settings.cache_clear()


def test_run_one_store_records_a_sync_run_reflecting_the_outcome(db_session, two_stores_with_users, monkeypatch):
    import app.services.product_catalog_scheduler as scheduler_module

    d = two_stores_with_users
    creds = OzonCredentials(
        store_id=d["store_a"].id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"),
    )
    db_session.add(creds)
    db_session.commit()

    monkeypatch.setattr(
        scheduler_module, "sync_product_catalog",
        lambda db, *, store_id, client: ProductCatalogSyncOutcome(fetched=5, created=5, updated=0, errors=[]),
    )

    scheduler_module._run_one_store(db_session, creds)

    run = (
        db_session.query(SyncRun)
        .filter(SyncRun.store_id == d["store_a"].id, SyncRun.source_type == SyncSourceType.OZON_PRODUCTS_API)
        .one()
    )
    assert run.status.value == "success"
    assert run.items_created == 5
    assert run.error_message is None


def test_run_one_store_marks_run_failed_on_hard_failure(db_session, two_stores_with_users, monkeypatch):
    """An auth/feature-tier failure (hard_failure=True) is always FAILED,
    never PARTIAL, even if some rows were created/updated before it hit."""
    import app.services.product_catalog_scheduler as scheduler_module

    d = two_stores_with_users
    creds = OzonCredentials(
        store_id=d["store_a"].id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"),
    )
    db_session.add(creds)
    db_session.commit()

    monkeypatch.setattr(
        scheduler_module, "sync_product_catalog",
        lambda db, *, store_id, client: ProductCatalogSyncOutcome(
            fetched=3, created=3, updated=0, errors=["Неверные учётные данные"], hard_failure=True,
        ),
    )

    scheduler_module._run_one_store(db_session, creds)

    run = (
        db_session.query(SyncRun)
        .filter(SyncRun.store_id == d["store_a"].id, SyncRun.source_type == SyncSourceType.OZON_PRODUCTS_API)
        .one()
    )
    assert run.status.value == "failed"
    assert "Неверные учётные данные" in run.error_message


def test_run_for_all_stores_only_processes_stores_with_seller_credentials(db_session, two_stores_with_users, monkeypatch):
    import app.services.product_catalog_scheduler as scheduler_module

    d = two_stores_with_users
    db_session.add(
        OzonCredentials(store_id=d["store_a"].id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"))
    )
    db_session.add(OzonCredentials(store_id=d["store_b"].id))  # no Seller API creds
    db_session.commit()

    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    processed: list[str] = []
    monkeypatch.setattr(scheduler_module, "_run_one_store", lambda db, creds: processed.append(creds.store_id))

    scheduler_module.run_product_catalog_sync_for_all_stores()

    assert processed == [d["store_a"].id]
