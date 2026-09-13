"""Route-level tests for the realization-report manual trigger
(POST .../sync/ozon-realization-report) — role gating, explicit year/month,
default backfill mode, and the "not yet available" (current month) case
treated as a normal outcome, not a failure. Only the Ozon Seller API client
itself is a fake, duck-typed stand-in, matching every other sync endpoint's
test conventions."""
from app.core.encryption import encrypt_secret
from app.models.realization_report_month import RealizationReportMonth
from app.services.ozon.exceptions import OzonFeatureUnavailable
from tests.conftest import login


class _FakeSellerClient:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_realization_report(self, *, year, month):
        raise NotImplementedError  # overridden per test


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
    viewer = User(email="realization_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "realization_viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-realization-report")
    assert resp.status_code == 403


def test_sync_without_seller_credentials_returns_400(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-realization-report")
    assert resp.status_code == 400


def test_sync_rejects_year_without_month(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-realization-report", params={"year": 2026})
    assert resp.status_code == 400


def test_sync_explicit_month_archives_the_report(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes

    class _FakeClientWithData(_FakeSellerClient):
        def get_realization_report(self, *, year, month):
            return {"result": {"rows": [{"offer_id": "art-1", "seller_price_per_instance": 1000}]}}

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeClientWithData)
    _patch_session(monkeypatch, db_session)

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)

    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/sync/ozon-realization-report",
        params={"year": 2026, "month": 8},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["items_fetched"] == 1
    assert body["items_created"] == 1

    row = db_session.query(RealizationReportMonth).filter(
        RealizationReportMonth.store_id == d["store_a"].id, RealizationReportMonth.year == 2026, RealizationReportMonth.month == 8,
    ).one()
    assert "art-1" in row.raw_payload


def test_sync_current_month_not_yet_available_is_a_success_not_a_failure(client, db_session, two_stores_with_users, monkeypatch):
    """CONFIRMED 2026-09-13: Ozon answers 404 "Report was not found" for
    the current, still-open month — this is an ordinary, expected outcome
    a seller shouldn't see as a red "failed" sync run."""
    import app.api.routes.sync as sync_routes

    class _FakeClientNotYet(_FakeSellerClient):
        def get_realization_report(self, *, year, month):
            raise OzonFeatureUnavailable("Ozon вернул 404 на /v2/finance/realization: Report was not found")

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeClientNotYet)
    _patch_session(monkeypatch, db_session)

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)

    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/sync/ozon-realization-report",
        params={"year": 2026, "month": 9},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["items_fetched"] == 0
    assert "Ещё не готов" in body["error_message"]
    assert db_session.query(RealizationReportMonth).count() == 0


def test_sync_default_backfill_skips_already_archived_months(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    db_session.add(RealizationReportMonth(store_id=d["store_a"].id, year=2026, month=8, raw_payload="{}", source="ozon_seller_api"))
    db_session.commit()

    calls = []

    class _FakeClientTracksCalls(_FakeSellerClient):
        def get_realization_report(self, *, year, month):
            calls.append((year, month))
            return {"result": {"rows": []}}

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeClientTracksCalls)
    _patch_session(monkeypatch, db_session)

    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-realization-report")
    assert resp.status_code == 200
    assert (2026, 8) not in calls  # already archived — must not be re-fetched
