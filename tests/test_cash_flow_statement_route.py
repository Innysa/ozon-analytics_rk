"""Route-level tests for the cash-flow-statement auto-sync: manual trigger
(POST .../sync/ozon-cash-flow-statement) — role gating, store isolation,
and a real end-to-end run through
app.services.cash_flow_statement_sync_service (not mocked out) whose result
is then verified via the Дашборд's own LogisticsBlock. Only the Ozon Seller
API client itself is a fake, duck-typed stand-in, matching every other sync
endpoint's test conventions."""
from app.core.encryption import encrypt_secret
from tests.conftest import login


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

    def get_cash_flow_statement(self, **kwargs):
        return {
            "result": {
                "cash_flows": [
                    {
                        "period": {"id": 0, "begin": "2026-09-07T00:00:00Z", "end": "2026-09-13T00:00:00Z"},
                        "orders_amount": 5197686, "returns_amount": -150571, "commission_amount": -2497441.46,
                        "services_amount": -464633.21, "item_delivery_and_return_amount": -270329.95, "currency_code": "RUB",
                    }
                ],
                "page_count": 1,
                "details": [
                    {
                        "period": {"id": 0, "begin": "2026-09-07T00:00:00Z", "end": "2026-09-13T00:00:00Z"},
                        "begin_balance_amount": 7546644.61,
                        "payments": [{"payment": -1811395.24, "currency_code": "RUB"}],
                        "delivery": {
                            "total": 1137907.3, "amount": 1259092.6,
                            "delivery_services": {
                                "total": -121185.3,
                                "items": [{"name": "MarketplaceServiceItemDirectFlowLogisticSum", "price": -112007.9}],
                            },
                        },
                        # TOP-LEVEL sibling of "delivery" — CONFIRMED
                        # 2026-09-12 on a real account (located via
                        # inspect_cash_flow_periods.py --find-key), NOT
                        # nested inside "delivery" as an earlier,
                        # unconfirmed guess had it.
                        "return": {
                            "total": -81414.52, "amount": -51662.52,
                            "items": [{"name": "MarketplaceServiceItemRedistributionReturnsPVZ", "price": -1650}],
                            "return_services": {
                                "total": -29752,
                                "items": [{"name": "MarketplaceServiceItemReturnFlowLogistic", "price": -28102}],
                            },
                        },
                        "loan": 0, "invoice_transfer": 15717.64,
                        "rfbs": {"total": 0, "transfer_delivery": 0, "transfer_delivery_return": 0, "compensation_delivery_return": 0, "partial_compensation": 0, "partial_compensation_return": 0},
                        "services": {"total": -93228.57, "items": [{"name": "MarketplaceServiceCostPerClick", "price": -81971.47}]},
                    }
                ],
            }
        }


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
    viewer = User(email="cf_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "cf_viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-cash-flow-statement")
    assert resp.status_code == 403


def test_sync_without_seller_credentials_returns_400(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-cash-flow-statement")
    assert resp.status_code == 400


def test_sync_end_to_end_and_dashboard_logistics_block(client, db_session, two_stores_with_users, monkeypatch):
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
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/sync/ozon-cash-flow-statement",
        params={"date_from": "2026-09-01", "date_to": "2026-09-13"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_type"] == "ozon_cash_flow_statement_api"
    assert body["status"] == "running"

    runs = client.get(f"/api/stores/{d['store_a'].id}/sync/runs")
    assert runs.status_code == 200
    finished_run = next(r for r in runs.json() if r["id"] == body["id"])
    assert finished_run["status"] in ("success", "partial")
    assert finished_run["items_created"] == 1

    dash = client.get(
        f"/api/stores/{d['store_a'].id}/dashboard",
        params={"date_from": "2026-09-01", "date_to": "2026-09-13"},
    )
    assert dash.status_code == 200
    logistics = dash.json()["logistics"]
    assert logistics["has_data"] is True
    assert logistics["periods_summed"] == 1
    assert logistics["logistics_rub"] == -121185.3
    assert logistics["returns_logistics_rub"] == -81414.52
    assert logistics["other_services_rub"] == -93228.57


def test_store_isolation(client, db_session, two_stores_with_users, monkeypatch):
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
        f"/api/stores/{d['store_a'].id}/sync/ozon-cash-flow-statement",
        params={"date_from": "2026-09-01", "date_to": "2026-09-13"},
    )

    login(client, "owner_b@example.com", "password123")
    dash = client.get(f"/api/stores/{d['store_b'].id}/dashboard", params={"date_from": "2026-09-01", "date_to": "2026-09-13"})
    assert dash.status_code == 200
    assert dash.json()["logistics"]["has_data"] is False
