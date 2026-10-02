"""Tests for app.services.warehouse_stock_sync_service — per-warehouse FBO
stock from POST /v2/analytics/stock_on_warehouses (CONFIRMED live
2026-10-02, see that module's own docstring and OzonSellerClient.
get_stock_on_warehouses's own docstring)."""
from app.models.product_warehouse_stock import ProductWarehouseStock
from app.services.ozon.exceptions import OzonAuthError
from app.services.warehouse_stock_sync_service import sync_warehouse_stocks
from tests.conftest import login


class _FakeClient:
    def __init__(self, pages: list[list[dict]]):
        self._pages = pages
        self.calls: list[dict] = []

    def get_stock_on_warehouses(self, *, limit: int, offset: int, warehouse_type: str = "ALL"):
        self.calls.append({"limit": limit, "offset": offset})
        page_index = offset // limit
        items = self._pages[page_index] if page_index < len(self._pages) else []
        return {"result": items}


class _RaisingClient:
    def get_stock_on_warehouses(self, *, limit: int, offset: int, warehouse_type: str = "ALL"):
        raise OzonAuthError("bad key")


def _item(sku=111, warehouse_id=1, warehouse_name="ВОРОНЕЖ_РФЦ", free=10, reserved=2, promised=0):
    return {
        "sku": sku,
        "item_code": "offer-111",
        "item_name": "Товар",
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse_name,
        "cluster_id": 16,
        "cluster_name": "Центр",
        "free_to_sell_amount": free,
        "reserved_amount": reserved,
        "promised_amount": promised,
    }


def test_sync_creates_rows(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient([[_item(warehouse_id=1), _item(warehouse_id=2, warehouse_name="УФА_РФЦ")]])

    outcome = sync_warehouse_stocks(db_session, store_id=store_id, client=client)
    db_session.commit()

    assert outcome.fetched == 2
    assert outcome.upserted == 2
    assert not outcome.errors
    rows = db_session.query(ProductWarehouseStock).filter(ProductWarehouseStock.store_id == store_id).all()
    assert len(rows) == 2
    assert {r.warehouse_name for r in rows} == {"ВОРОНЕЖ_РФЦ", "УФА_РФЦ"}


def test_sync_is_a_full_replace_snapshot(db_session, two_stores_with_users):
    """A SKU/warehouse missing from the latest Ozon response must not
    linger as a stale row — see ProductWarehouseStock's own docstring."""
    d = two_stores_with_users
    store_id = d["store_a"].id

    first_client = _FakeClient([[_item(warehouse_id=1), _item(warehouse_id=2)]])
    sync_warehouse_stocks(db_session, store_id=store_id, client=first_client)
    db_session.commit()
    assert db_session.query(ProductWarehouseStock).filter(ProductWarehouseStock.store_id == store_id).count() == 2

    second_client = _FakeClient([[_item(warehouse_id=1, free=5)]])
    outcome = sync_warehouse_stocks(db_session, store_id=store_id, client=second_client)
    db_session.commit()

    rows = db_session.query(ProductWarehouseStock).filter(ProductWarehouseStock.store_id == store_id).all()
    assert outcome.upserted == 1
    assert len(rows) == 1
    assert rows[0].warehouse_id == 1
    assert rows[0].free_to_sell_amount == 5


def test_sync_paginates_until_a_short_page(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    full_page = [_item(warehouse_id=i) for i in range(1000)]
    short_page = [_item(warehouse_id=1000)]
    client = _FakeClient([full_page, short_page])

    outcome = sync_warehouse_stocks(db_session, store_id=store_id, client=client)

    assert outcome.fetched == 1001
    assert len(client.calls) == 2
    assert client.calls[1]["offset"] == 1000


def test_sync_handles_empty_response(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient([[]])

    outcome = sync_warehouse_stocks(db_session, store_id=store_id, client=client)

    assert outcome.fetched == 0
    assert outcome.upserted == 0
    assert any("не вернул" in e for e in outcome.errors)


def test_sync_surfaces_auth_error_as_hard_failure(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id

    outcome = sync_warehouse_stocks(db_session, store_id=store_id, client=_RaisingClient())

    assert outcome.hard_failure is True
    assert outcome.upserted == 0
    assert any("bad key" in e for e in outcome.errors)


def test_upload_route_requires_manager_role(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    viewer = User(email="stock_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "stock_viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-warehouse-stocks")
    assert resp.status_code == 403


def test_list_route_requires_ozon_credentials_for_sync(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-warehouse-stocks")
    assert resp.status_code == 400


def test_list_route_returns_empty_before_any_sync(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/warehouse-stocks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["synced_at"] is None
