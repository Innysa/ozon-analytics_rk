"""Route-level tests for the automatic orders sync: manual trigger
(POST .../sync/ozon-orders) and the listing endpoint
(GET .../orders/daily-statistics) — role gating, store isolation, and a
real end-to-end run through app.services.order_daily_sync_service (not
mocked out), matching every other sync endpoint's test conventions. Only
the Ozon Seller API client itself is a fake, duck-typed stand-in."""
from app.core.encryption import encrypt_secret
from app.services.ozon.schemas import OzonPostingItem, OzonPostingListResponse, OzonPostingListResult, OzonPostingProductItem
from tests.conftest import login


def _posting(status: str, day: str, sku: int, price: str, old_price: float, commission: float) -> OzonPostingItem:
    return OzonPostingItem(
        posting_number="777-0001-1",
        status=status,
        in_process_at=f"{day}T10:00:00.000000Z",
        products=[OzonPostingProductItem(sku=sku, offer_id="art-777", name="Товар 777", quantity=1, price=price)],
        financial_data={"products": [{"product_id": sku, "old_price": old_price, "price": float(price), "commission_amount": commission}]},
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

    def list_fbo_postings(self, *, date_from, date_to, offset=0, limit=1000):
        if offset > 0:
            return OzonPostingListResponse(result=OzonPostingListResult(postings=[], has_next=False))
        posting = _posting("delivered", "2026-09-01", 777, "1000.00", 1500.0, -100)
        return OzonPostingListResponse(result=OzonPostingListResult(postings=[posting], has_next=False))

    def list_fbs_postings(self, *, date_from, date_to, offset=0, limit=1000):
        return OzonPostingListResponse(result=OzonPostingListResult(postings=[], has_next=False))


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
    viewer = User(email="orders_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "orders_viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-orders")
    assert resp.status_code == 403


def test_sync_without_seller_credentials_returns_400(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-orders")
    assert resp.status_code == 400


def test_sync_end_to_end_creates_order_daily_statistics_and_syncrun(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes
    from app.db.session import SessionLocal as RealSessionLocal

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

    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/sync/ozon-orders",
        params={"date_from": "2026-09-01", "date_to": "2026-09-01"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_type"] == "ozon_orders_api"
    assert body["status"] == "running"

    runs = client.get(f"/api/stores/{d['store_a'].id}/sync/runs")
    assert runs.status_code == 200
    finished_run = next(r for r in runs.json() if r["id"] == body["id"])
    assert finished_run["status"] in ("success", "partial")
    assert finished_run["items_created"] == 1  # one FBO row created (FBS returned nothing)

    listing = client.get(f"/api/stores/{d['store_a'].id}/orders/daily-statistics")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["delivery_schema"] == "FBO"
    assert items[0]["date"] == "2026-09-01"
    assert items[0]["ordered_units"] == 1
    assert items[0]["ordered_sum_rub"] == 1500.0
    assert items[0]["delivered_units"] == 1
    assert items[0]["delivered_sum_rub"] == 1000.0
    assert items[0]["commission_rub"] == -100.0


def test_store_isolation_on_daily_statistics_listing(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes

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

    login(client, "owner_a@example.com", "password123")
    client.post(
        f"/api/stores/{d['store_a'].id}/sync/ozon-orders",
        params={"date_from": "2026-09-01", "date_to": "2026-09-01"},
    )

    forbidden = client.get(f"/api/stores/{d['store_b'].id}/orders/daily-statistics")
    assert forbidden.status_code == 403

    login(client, "owner_b@example.com", "password123")
    empty = client.get(f"/api/stores/{d['store_b'].id}/orders/daily-statistics")
    assert empty.status_code == 200
    assert empty.json()["items"] == []
