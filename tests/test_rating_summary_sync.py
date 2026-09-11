"""Route-level tests for the store-wide «Локализация» sync (manual trigger
POST .../sync/ozon-rating-summary) — role gating, store isolation, and a
real end-to-end run through the route (not mocked out beyond the Ozon
client itself), matching every other sync endpoint's test conventions. This
is a synchronous route (no BackgroundTasks) since it's a single fast call —
see app.api.routes.sync.sync_ozon_rating_summary's own docstring."""
from app.core.encryption import encrypt_secret
from tests.conftest import login


class _FakeSellerClient:
    """Stands in for OzonSellerClient as a context manager, matching the
    real class's `with OzonSellerClient(...) as client:` usage in
    app.api.routes.sync."""

    response: dict = {
        "localization_index": {"localization_percentage": 65, "calculation_date": "2026-09-04T00:00:00Z"},
        "premium": True,
    }

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_rating_summary(self):
        return self.response


class _FakeSellerClientNoSales(_FakeSellerClient):
    """Empty localization_index — the "no sales in the last 14 days" case
    a (separate) Ozon support answer described."""

    response = {"premium": True}


def _setup_store_with_seller_creds(db_session, store_id: str):
    from app.models.ozon_credentials import OzonCredentials

    creds = OzonCredentials(store_id=store_id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"))
    db_session.add(creds)
    db_session.commit()


def test_sync_requires_manager_role(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    viewer = User(email="rating_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "rating_viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-rating-summary")
    assert resp.status_code == 403


def test_sync_without_seller_credentials_returns_400(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-rating-summary")
    assert resp.status_code == 400


def test_sync_end_to_end_populates_total_row(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes
    from app.models.product import Product

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeSellerClient)

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    db_session.add(Product(store_id=d["store_a"].id, ozon_sku="SKU-A-1", name="Товар А"))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-rating-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_type"] == "ozon_rating_summary_api"
    assert body["status"] == "success"
    assert body["items_created"] == 1

    from app.models.store_rating_summary import StoreRatingSummary

    row = db_session.query(StoreRatingSummary).filter(StoreRatingSummary.store_id == d["store_a"].id).one()
    assert float(row.localization_pct) == 65
    assert row.localization_calculation_date.year == 2026
    assert row.localization_calculation_date.month == 9
    assert row.localization_calculation_date.day == 4

    planner = client.get(f"/api/stores/{d['store_a'].id}/product-planner")
    assert planner.status_code == 200
    total = planner.json()["total"]
    assert total is not None
    assert total["localization_pct"] == 65
    assert total["localization_calculation_date"] == "2026-09-04"


def test_sync_upserts_not_duplicates_on_second_run(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeSellerClient)

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)

    login(client, "owner_a@example.com", "password123")
    client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-rating-summary")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-rating-summary")
    assert resp.status_code == 200
    assert resp.json()["items_skipped_duplicate"] == 1

    from app.models.store_rating_summary import StoreRatingSummary

    count = db_session.query(StoreRatingSummary).filter(StoreRatingSummary.store_id == d["store_a"].id).count()
    assert count == 1


def test_sync_with_empty_localization_index_is_partial_not_a_crash(client, db_session, two_stores_with_users, monkeypatch):
    """The "no sales in the last 14 days" case — localization_index absent
    entirely. Must not crash, and must not be reported as a full success
    (nothing usable was actually fetched)."""
    import app.api.routes.sync as sync_routes

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeSellerClientNoSales)

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)

    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-rating-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "partial"
    assert body["error_message"]


def test_store_isolation(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes
    from app.models.product import Product

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeSellerClient)

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    db_session.add(Product(store_id=d["store_b"].id, ozon_sku="SKU-B-1", name="Товар Б"))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-rating-summary")

    login(client, "owner_b@example.com", "password123")
    planner = client.get(f"/api/stores/{d['store_b'].id}/product-planner")
    assert planner.status_code == 200
    total = planner.json()["total"]
    assert total is not None
    assert total["localization_pct"] is None
