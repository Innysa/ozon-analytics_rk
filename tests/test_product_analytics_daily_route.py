"""Route-level tests for the per-product funnel auto-sync: manual trigger
(POST .../sync/ozon-product-analytics) and the listing endpoint
(GET .../product-analytics/auto) — role gating, store isolation, and a real
end-to-end run through app.services.product_analytics_daily_sync_service
(not mocked out). Only the Ozon Seller API client itself is a fake,
duck-typed stand-in, matching every other sync endpoint's test conventions."""
from app.core.encryption import encrypt_secret
from app.services.ozon.schemas import OzonAnalyticsDataResponse, OzonAnalyticsDataResult, OzonAnalyticsDataRow, OzonAnalyticsDimensionValue
from tests.conftest import login


def _row(sku: str, day: str, *, revenue=0, ordered_units=0, views=0, cart_adds=0, conv=0.0, sessions=0, position=0.0) -> OzonAnalyticsDataRow:
    return OzonAnalyticsDataRow(
        dimensions=[OzonAnalyticsDimensionValue(id=sku, name="Товар"), OzonAnalyticsDimensionValue(id=day, name="")],
        metrics=[revenue, ordered_units, views, cart_adds, conv, sessions, position],
    )


class _FakeSellerClient:
    """Stands in for OzonSellerClient as a context manager, matching the
    real class's `with OzonSellerClient(...) as client:` usage in
    app.api.routes.sync."""

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_analytics_data(self, **kwargs):
        return OzonAnalyticsDataResponse(
            result=OzonAnalyticsDataResult(
                data=[_row("777", "2026-09-01", revenue=1500, ordered_units=2, views=100, cart_adds=10, conv=10.0, sessions=80, position=5.0)],
                totals=[1500],
            ),
            timestamp="2026-09-01 00:00:00",
        )


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
    viewer = User(email="pa_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "pa_viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-product-analytics")
    assert resp.status_code == 403


def test_sync_without_seller_credentials_returns_400(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-product-analytics")
    assert resp.status_code == 400


def test_sync_end_to_end_and_listing(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes
    from app.models.product import Product

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeSellerClient)

    class _NoCloseSessionWrapper:
        def __init__(self, session):
            self._session = session

        def __getattr__(self, name):
            return getattr(self._session, name)

        def close(self):
            pass

    monkeypatch.setattr(sync_routes, "SessionLocal", lambda: _NoCloseSessionWrapper(db_session))

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    product = Product(store_id=d["store_a"].id, ozon_sku="777", name="Товар 777")
    db_session.add(product)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/sync/ozon-product-analytics",
        params={"date_from": "2026-09-01", "date_to": "2026-09-01"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_type"] == "ozon_analytics_data_api"
    assert body["status"] == "running"

    runs = client.get(f"/api/stores/{d['store_a'].id}/sync/runs")
    assert runs.status_code == 200
    finished_run = next(r for r in runs.json() if r["id"] == body["id"])
    assert finished_run["status"] in ("success", "partial")
    assert finished_run["items_created"] == 1

    listing = client.get(f"/api/stores/{d['store_a'].id}/product-analytics/auto?product_id={product.id}")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["date"] == "2026-09-01"
    assert items[0]["revenue_rub"] == 1500.0
    assert items[0]["views_pdp"] == 100
    assert items[0]["cart_adds_pdp"] == 10
    assert items[0]["cart_conversion_pdp_pct"] == 10.0


def test_unknown_product_id_returns_404(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/product-analytics/auto?product_id=does-not-exist")
    assert resp.status_code == 404


def test_store_isolation_on_listing(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes
    from app.models.product import Product

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeSellerClient)

    class _NoCloseSessionWrapper:
        def __init__(self, session):
            self._session = session

        def __getattr__(self, name):
            return getattr(self._session, name)

        def close(self):
            pass

    monkeypatch.setattr(sync_routes, "SessionLocal", lambda: _NoCloseSessionWrapper(db_session))

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    product_a = Product(store_id=d["store_a"].id, ozon_sku="777", name="Товар А")
    db_session.add(product_a)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-product-analytics", params={"date_from": "2026-09-01", "date_to": "2026-09-01"})

    # admin has access to both stores — exercises the actual
    # product-not-in-this-store 404 rather than the membership boundary 403.
    login(client, "admin@example.com", "adminpass123")
    resp = client.get(f"/api/stores/{d['store_b'].id}/product-analytics/auto?product_id={product_a.id}")
    assert resp.status_code == 404
