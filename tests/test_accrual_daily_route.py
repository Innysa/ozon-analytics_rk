"""Route-level tests for the accrual-daily manual trigger
(POST .../sync/ozon-accrual-daily) — role gating, missing credentials,
default trailing-window mode, and an explicit date_from/date_to range.
Only the Ozon Seller API client itself is a fake, duck-typed stand-in,
matching every other sync endpoint's test conventions."""
from datetime import date

from app.core.encryption import encrypt_secret
from app.models.accrual_daily_statistic import AccrualDailyStatistic
from tests.conftest import login


class _FakeSellerClient:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_accrual_by_day(self, *, day, page, page_size):
        raise NotImplementedError  # overridden per test

    def get_realization_by_day(self, *, year, month, day):
        return {"rows": []}  # not under test here — accrual_daily_sync_service tests cover this


def _setup_store_with_seller_creds(db_session, store_id: str):
    from app.models.ozon_credentials import OzonCredentials

    creds = OzonCredentials(store_id=store_id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"))
    db_session.add(creds)
    db_session.commit()


def _patch_session(monkeypatch, db_session):
    import app.api.routes.sync as sync_routes

    class _NoCloseSessionWrapper:
        def __init__(self, session):
            self._session = session

        def __getattr__(self, name):
            return getattr(self._session, name)

        def close(self):
            pass

    monkeypatch.setattr(sync_routes, "SessionLocal", lambda: _NoCloseSessionWrapper(db_session))


def test_sync_requires_manager_role(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    viewer = User(email="accrual_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "accrual_viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-accrual-daily")
    assert resp.status_code == 403


def test_sync_without_seller_credentials_returns_400(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-accrual-daily")
    assert resp.status_code == 400


def test_sync_rejects_date_from_without_date_to(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/sync/ozon-accrual-daily",
        params={"date_from": "2026-09-01"},
    )
    assert resp.status_code == 400


def test_sync_default_mode_resyncs_the_trailing_window(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes
    from app.core import config as config_module

    calls = []

    class _FakeClientTracksCalls(_FakeSellerClient):
        def get_accrual_by_day(self, *, day, page, page_size):
            calls.append(day)
            return {"result": {"records": [
                {"accrual_id": "a1", "total_amount": {"amount": "10.00"}, "accrued_category": "ITEM"},
            ]}}

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeClientTracksCalls)
    monkeypatch.setenv("ACCRUAL_DAILY_TRAILING_DAYS", "2")
    config_module.get_settings.cache_clear()
    _patch_session(monkeypatch, db_session)

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)

    login(client, "owner_a@example.com", "password123")
    try:
        resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-accrual-daily")
    finally:
        config_module.get_settings.cache_clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["items_fetched"] == 2
    assert body["items_created"] == 2
    assert len(calls) == 2  # one call per day in the trailing window (before pagination stop)


def test_sync_explicit_date_range_archives_each_day(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes

    class _FakeClientWithData(_FakeSellerClient):
        def get_accrual_by_day(self, *, day, page, page_size):
            return {"result": {"records": [
                {"accrual_id": f"a-{day}", "total_amount": {"amount": "42.00"}, "accrued_category": "POSTING"},
            ]}}

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeClientWithData)
    _patch_session(monkeypatch, db_session)

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)

    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/sync/ozon-accrual-daily",
        params={"date_from": "2026-09-10", "date_to": "2026-09-12"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["items_fetched"] == 3
    assert body["items_created"] == 3

    rows = db_session.query(AccrualDailyStatistic).filter(AccrualDailyStatistic.store_id == d["store_a"].id).all()
    assert {r.date for r in rows} == {date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)}
