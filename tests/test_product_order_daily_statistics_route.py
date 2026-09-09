"""Route-level tests for the per-product order statistics (product detail
page's "Продажи" tab, API-sourced block): GET .../orders/product-daily-
statistics, populated by the SAME sync run as .../orders/daily-statistics
(app.services.order_daily_sync_service) — no separate trigger, no separate
SyncRun. Only the Ozon Seller API client itself is a fake, duck-typed
stand-in, matching every other sync endpoint's test conventions."""
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
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_fbo_postings(self, *, date_from, date_to, offset=0, limit=1000):
        if offset > 0:
            return OzonPostingListResponse(result=OzonPostingListResult(postings=[], has_next=False))
        postings = [
            _posting("delivered", "2026-09-01", 777, "1000.00", 1500.0, -100),
            _posting("delivered", "2026-09-01", 888, "200.00", 250.0, -20),
        ]
        return OzonPostingListResponse(result=OzonPostingListResult(postings=postings, has_next=False))

    def list_fbs_postings(self, *, date_from, date_to, offset=0, limit=1000):
        return OzonPostingListResponse(result=OzonPostingListResult(postings=[], has_next=False))


def _setup_store_with_seller_creds(db_session, store_id: str):
    from app.models.ozon_credentials import OzonCredentials

    creds = OzonCredentials(store_id=store_id, client_id_encrypted=encrypt_secret("cid"), api_key_encrypted=encrypt_secret("key"))
    db_session.add(creds)
    db_session.commit()


class _NoCloseSessionWrapper:
    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


def test_sync_populates_per_product_table_sliced_by_sku(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes
    from app.models.product import Product

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeSellerClient)
    monkeypatch.setattr(sync_routes, "SessionLocal", lambda: _NoCloseSessionWrapper(db_session))

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    product_777 = Product(store_id=d["store_a"].id, ozon_sku="777", name="Товар 777")
    product_888 = Product(store_id=d["store_a"].id, ozon_sku="888", name="Товар 888")
    db_session.add_all([product_777, product_888])
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/sync/ozon-orders",
        params={"date_from": "2026-09-01", "date_to": "2026-09-01"},
    )
    assert resp.status_code == 200

    listing_777 = client.get(f"/api/stores/{d['store_a'].id}/orders/product-daily-statistics?product_id={product_777.id}")
    assert listing_777.status_code == 200
    items_777 = listing_777.json()["items"]
    assert len(items_777) == 1
    assert items_777[0]["delivery_schema"] == "FBO"
    assert items_777[0]["date"] == "2026-09-01"
    assert items_777[0]["ordered_units"] == 1
    assert items_777[0]["ordered_sum_rub"] == 1500.0
    assert items_777[0]["delivered_sum_rub"] == 1000.0
    assert items_777[0]["commission_rub"] == -100.0

    # Product 888's numbers must not leak into 777's listing or vice versa.
    listing_888 = client.get(f"/api/stores/{d['store_a'].id}/orders/product-daily-statistics?product_id={product_888.id}")
    assert listing_888.status_code == 200
    items_888 = listing_888.json()["items"]
    assert len(items_888) == 1
    assert items_888[0]["delivered_sum_rub"] == 200.0


def test_unknown_product_id_returns_404(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/orders/product-daily-statistics?product_id=does-not-exist")
    assert resp.status_code == 404


def test_product_from_another_store_returns_404_not_leaked_data(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes
    from app.models.product import Product

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeSellerClient)
    monkeypatch.setattr(sync_routes, "SessionLocal", lambda: _NoCloseSessionWrapper(db_session))

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    product_a = Product(store_id=d["store_a"].id, ozon_sku="777", name="Товар А")
    db_session.add(product_a)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-orders", params={"date_from": "2026-09-01", "date_to": "2026-09-01"})

    # admin has access to both stores — exercises the actual
    # product-not-in-this-store 404 rather than the membership boundary 403.
    login(client, "admin@example.com", "adminpass123")
    resp = client.get(f"/api/stores/{d['store_b'].id}/orders/product-daily-statistics?product_id={product_a.id}")
    assert resp.status_code == 404
