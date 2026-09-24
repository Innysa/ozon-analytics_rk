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


def test_sync_returns_409_when_a_sync_is_already_running(client, db_session, two_stores_with_users):
    """Regression test for a real production race (2026-09-11): two
    concurrent order-sync runs for the same store (a manual click while
    another was still finishing) had no mutual exclusion — REPLACE-style
    upserts mean whichever run commits LAST simply wins, even with older
    data than the other. See order_daily_sync_service.find_blocking_running_sync's
    own docstring."""
    from datetime import datetime, timedelta, timezone

    from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus

    d = two_stores_with_users
    _setup_store_with_seller_creds(db_session, d["store_a"].id)
    db_session.add(SyncRun(
        store_id=d["store_a"].id, source_type=SyncSourceType.OZON_ORDERS_API, status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-orders")
    assert resp.status_code == 409


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
    # Regression, CONFIRMED 2026-09-12: SyncRun.items_created/items_fetched
    # combine FBO+FBS into one number, which isn't enough to tell "FBS
    # genuinely fetched 0" apart from "FBS silently lost real data" — a
    # real account's own re-check ("получено 556, создано 0, обновлено 1")
    # was misread as evidence of a bug for exactly this reason. The
    # per-schema breakdown note must be present even on a clean success.
    assert "FBO=1" in finished_run["error_message"]
    assert "FBS=0" in finished_run["error_message"]

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


def test_listing_overrides_commission_with_confirmed_accrual_figure(client, db_session, two_stores_with_users):
    """CONFIRMED 2026-09-14: AccrualDailyStatistic.commission_ozon_rub is
    Ozon's own exact figure — the "РНП" page's commission column must show
    it instead of OrderDailyStatistic's own postings-derived estimate for
    any day it's been synced, same swap the Dashboard's margin block
    makes (see dashboard_service._commission_rub_for_period)."""
    from datetime import date

    from app.models.accrual_daily_statistic import AccrualDailyStatistic
    from app.models.order_daily_statistic import OrderDailyStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 9, 12), delivery_schema="FBO",
        ordered_units=1, ordered_sum_rub=1000, ordered_sum_discounted_rub=1000,
        delivered_units=1, delivered_sum_rub=1000, cost_of_delivered_rub=0, cost_of_delivered_known_units=0,
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=-100,
        source="ozon_seller_api",
    ))
    db_session.add(AccrualDailyStatistic(
        store_id=store_id, date=date(2026, 9, 12), total_amount_rub=0,
        by_category_json="{}", record_count=1, commission_ozon_rub=-389940.02,
        source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/orders/daily-statistics")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["commission_rub"] == -389940.02


def test_listing_does_not_double_count_accrual_commission_across_fbo_and_fbs_rows(client, db_session, two_stores_with_users):
    """A day with both an FBO and an FBS row must get the whole-day
    accrual figure applied to only ONE of them and zeroed on the other —
    the "РНП" page sums commission_rub across every row for a date
    client-side, so applying it to both would double it."""
    from datetime import date

    from app.models.accrual_daily_statistic import AccrualDailyStatistic
    from app.models.order_daily_statistic import OrderDailyStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 9, 12), delivery_schema="FBO",
        ordered_units=1, ordered_sum_rub=1000, ordered_sum_discounted_rub=1000,
        delivered_units=1, delivered_sum_rub=1000, cost_of_delivered_rub=0, cost_of_delivered_known_units=0,
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=-60,
        source="ozon_seller_api",
    ))
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 9, 12), delivery_schema="FBS",
        ordered_units=1, ordered_sum_rub=800, ordered_sum_discounted_rub=800,
        delivered_units=1, delivered_sum_rub=800, cost_of_delivered_rub=0, cost_of_delivered_known_units=0,
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=-40,
        source="ozon_seller_api",
    ))
    db_session.add(AccrualDailyStatistic(
        store_id=store_id, date=date(2026, 9, 12), total_amount_rub=0,
        by_category_json="{}", record_count=2, commission_ozon_rub=-300.0,
        source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/orders/daily-statistics")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    total_commission = sum(item["commission_rub"] for item in items)
    assert total_commission == -300.0  # NOT -600.0


def test_listing_prefers_funnel_ordered_units_over_postings_for_a_synced_date(client, db_session, two_stores_with_users):
    """ПЕРЕКЛЮЧЕНО 2026-09-24: same switch as «РНП Товары» (see
    product_planner_service's module docstring), applied to the store-level
    "РНП" day table too — the user found a day's «Заказано, шт» here still
    looked wrong right after we'd already fixed the per-product page, and
    expected the fix to apply everywhere «Заказано» is shown. Store-wide
    funnel units are the SUM of ProductAnalyticsDailyStatistic.ordered_units
    across every SKU for that date. ordered_sum_rub must stay untouched —
    same unconfirmed-price-basis reasoning as the per-product switch."""
    from datetime import date

    from app.models.order_daily_statistic import OrderDailyStatistic
    from app.models.product_analytics_daily_statistic import ProductAnalyticsDailyStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 9, 22), delivery_schema="FBO",
        ordered_units=10, ordered_sum_rub=2000, ordered_sum_discounted_rub=2000,
        delivered_units=8, delivered_sum_rub=1600, cost_of_delivered_rub=0, cost_of_delivered_known_units=0,
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=-100,
        source="ozon_seller_api",
    ))
    db_session.add(ProductAnalyticsDailyStatistic(
        store_id=store_id, ozon_sku="SKU-1", date=date(2026, 9, 22),
        revenue_rub=1000, ordered_units=9, views_pdp=0, cart_adds_pdp=0, sessions_pdp=0,
        source="ozon_seller_api",
    ))
    db_session.add(ProductAnalyticsDailyStatistic(
        store_id=store_id, ozon_sku="SKU-2", date=date(2026, 9, 22),
        revenue_rub=2000, ordered_units=6, views_pdp=0, cart_adds_pdp=0, sessions_pdp=0,
        source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/orders/daily-statistics")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["ordered_units"] == 15  # 9 + 6 from the funnel, NOT the postings' 10
    assert items[0]["ordered_sum_rub"] == 2000  # untouched — postings, not funnel revenue


def test_listing_falls_back_to_postings_units_on_a_date_the_funnel_has_not_synced(client, db_session, two_stores_with_users):
    from datetime import date

    from app.models.order_daily_statistic import OrderDailyStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 9, 23), delivery_schema="FBO",
        ordered_units=4, ordered_sum_rub=800, ordered_sum_discounted_rub=800,
        delivered_units=3, delivered_sum_rub=600, cost_of_delivered_rub=0, cost_of_delivered_known_units=0,
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=-40,
        source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/orders/daily-statistics")
    items = resp.json()["items"]
    assert items[0]["ordered_units"] == 4  # no funnel row for this date — postings kept


def test_listing_does_not_double_count_funnel_units_across_fbo_and_fbs_rows(client, db_session, two_stores_with_users):
    from datetime import date

    from app.models.order_daily_statistic import OrderDailyStatistic
    from app.models.product_analytics_daily_statistic import ProductAnalyticsDailyStatistic

    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 9, 22), delivery_schema="FBO",
        ordered_units=5, ordered_sum_rub=500, ordered_sum_discounted_rub=500,
        delivered_units=4, delivered_sum_rub=400, cost_of_delivered_rub=0, cost_of_delivered_known_units=0,
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=-20,
        source="ozon_seller_api",
    ))
    db_session.add(OrderDailyStatistic(
        store_id=store_id, date=date(2026, 9, 22), delivery_schema="FBS",
        ordered_units=3, ordered_sum_rub=300, ordered_sum_discounted_rub=300,
        delivered_units=2, delivered_sum_rub=200, cost_of_delivered_rub=0, cost_of_delivered_known_units=0,
        cancelled_units=0, cancelled_sum_rub=0, unfinished_units=0, commission_rub=-10,
        source="ozon_seller_api",
    ))
    db_session.add(ProductAnalyticsDailyStatistic(
        store_id=store_id, ozon_sku="SKU-1", date=date(2026, 9, 22),
        revenue_rub=1000, ordered_units=12, views_pdp=0, cart_adds_pdp=0, sessions_pdp=0,
        source="ozon_seller_api",
    ))
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{store_id}/orders/daily-statistics")
    items = resp.json()["items"]
    assert len(items) == 2
    total_units = sum(item["ordered_units"] for item in items)
    assert total_units == 12  # NOT 12 + 12 = 24


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
