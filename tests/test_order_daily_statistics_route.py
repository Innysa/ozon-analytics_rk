"""Route-level tests for the automatic orders sync: manual trigger
(POST .../sync/ozon-orders) and the listing endpoint
(GET .../orders/daily-statistics) — role gating, store isolation, and a
real end-to-end run through app.services.order_daily_sync_service (not
mocked out), matching every other sync endpoint's test conventions. Only
the Ozon Seller API client itself is a fake, duck-typed stand-in."""
from app.core.encryption import encrypt_secret
from app.services.ozon.exceptions import OzonRateLimited
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


def test_sync_notes_postings_skipped_for_missing_in_process_at_even_on_success(client, db_session, two_stores_with_users, monkeypatch):
    """Regression test for a real production investigation (2026-09-11):
    aggregate_postings_by_day()/aggregate_postings_by_sku_and_day() silently
    drop any posting whose in_process_at is empty — a real, not hypothetical,
    way for recent orders to go missing from every day's totals without the
    sync ever reporting an error. SyncOutcome.skipped_no_process_date (and
    skipped_no_process_date_note()) surface this as an informational note on
    SyncRun.error_message even when the run is otherwise a clean SUCCESS —
    see order_daily_sync_service.py's own docstring for the reasoning."""
    import app.api.routes.sync as sync_routes

    class _FakeSellerClientWithNullDatePosting(_FakeSellerClient):
        def list_fbo_postings(self, *, date_from, date_to, offset=0, limit=1000):
            if offset > 0:
                return OzonPostingListResponse(result=OzonPostingListResult(postings=[], has_next=False))
            dated = _posting("delivered", "2026-09-01", 777, "1000.00", 1500.0, -100)
            not_yet_processed = OzonPostingItem(
                posting_number="777-0002-1",
                status="awaiting_packaging",
                in_process_at=None,
                products=[OzonPostingProductItem(sku=777, offer_id="art-777", name="Товар 777", quantity=1, price="1000.00")],
            )
            return OzonPostingListResponse(result=OzonPostingListResult(postings=[dated, not_yet_processed], has_next=False))

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeSellerClientWithNullDatePosting)

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

    runs = client.get(f"/api/stores/{d['store_a'].id}/sync/runs")
    finished_run = next(r for r in runs.json() if r["id"] == body["id"])
    assert finished_run["status"] == "success"  # the missing-date posting is NOT an error
    assert finished_run["items_fetched"] == 2  # both postings were fetched...
    assert "1" in finished_run["error_message"]  # ...but the note says 1 was skipped for it


def test_sync_splits_window_into_chunks_and_survives_one_chunk_failing(client, db_session, two_stores_with_users, monkeypatch):
    """Regression test for a real production finding (2026-09-11): a
    single request covering the full lookback window intermittently hit
    sustained 429s on FBO for one real account, even with an already-more-
    patient retry. sync_order_daily_statistics() now splits the window into
    ORDER_STATS_SYNC_CHUNK_DAYS-sized pieces (see _date_chunks) and fetches
    each independently — this proves the key property that motivated it:
    ONE chunk exhausting retries and failing must NOT discard postings
    already fetched from OTHER chunks in the same run. Uses a 10-day window
    with the default chunk size (5 days) -> exactly 2 chunks, the first of
    which always 429s."""
    import app.api.routes.sync as sync_routes

    class _FakeSellerClientOneChunkFails(_FakeSellerClient):
        def list_fbo_postings(self, *, date_from, date_to, offset=0, limit=1000):
            if date_from.startswith("2026-09-01"):
                raise OzonRateLimited("Ozon вернул 429 Too Many Requests")
            if offset > 0:
                return OzonPostingListResponse(result=OzonPostingListResult(postings=[], has_next=False))
            posting = _posting("delivered", "2026-09-07", 777, "1000.00", 1500.0, -100)
            return OzonPostingListResponse(result=OzonPostingListResult(postings=[posting], has_next=False))

    monkeypatch.setattr(sync_routes, "OzonSellerClient", _FakeSellerClientOneChunkFails)

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
        params={"date_from": "2026-09-01", "date_to": "2026-09-10"},
    )
    assert resp.status_code == 200
    body = resp.json()

    runs = client.get(f"/api/stores/{d['store_a'].id}/sync/runs")
    finished_run = next(r for r in runs.json() if r["id"] == body["id"])
    assert finished_run["status"] == "partial"
    assert "2026-09-01" in finished_run["error_message"]
    assert "429" in finished_run["error_message"]

    # The second chunk's data (Sep 7) still made it in, despite the first chunk failing.
    listing = client.get(f"/api/stores/{d['store_a'].id}/orders/daily-statistics")
    items = listing.json()["items"]
    assert any(item["date"] == "2026-09-07" and item["delivery_schema"] == "FBO" for item in items)


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
