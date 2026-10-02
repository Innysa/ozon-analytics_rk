"""Tests for app.services.supply_order_pending_sync_service — the
three-step chain (list -> get -> bundle) confirmed live 2026-10-02/03,
see that module's own docstring for the full backstory (promised_amount
on ProductWarehouseStock means "уже отгружено", not "не отгружено")."""
from app.models.product import Product
from app.services.ozon.exceptions import OzonAuthError
from app.services.supply_order_pending_sync_service import sync_supply_order_pending_quantities
from tests.conftest import login


class _FakeClient:
    def __init__(self, *, pages: list[list[int]], bundles: dict[int, list[dict]], bundle_ids: dict[int, str] | None = None):
        self._pages = pages
        self._bundles = bundles
        self._bundle_ids = bundle_ids or {}
        self.list_calls = 0
        self.get_calls: list[int] = []
        self.bundle_calls: list[str] = []

    def list_supply_orders(self, *, states, limit, last_id=""):
        page_index = self.list_calls
        self.list_calls += 1
        page = self._pages[page_index] if page_index < len(self._pages) else []
        is_last_page = page_index >= len(self._pages) - 1
        return {"order_ids": page, "last_id": "" if is_last_page else f"cursor-{page_index}"}

    def get_supply_order(self, order_id: int):
        self.get_calls.append(order_id)
        bundle_id = self._bundle_ids.get(order_id, f"bundle-{order_id}")
        return {"result": [{"content": {"bundle_id": bundle_id}}]}

    def get_supply_order_bundle(self, bundle_id: str, *, limit: int = 100):
        self.bundle_calls.append(bundle_id)
        for order_id, bid in self._bundle_ids.items():
            if bid == bundle_id:
                return {"result": self._bundles.get(order_id, [])}
        # fall back: match by the default f"bundle-{order_id}" scheme
        for order_id in self._bundles:
            if bundle_id == f"bundle-{order_id}":
                return {"result": self._bundles[order_id]}
        return {"result": []}


class _RaisingListClient:
    def list_supply_orders(self, *, states, limit, last_id=""):
        raise OzonAuthError("bad key")


def test_sync_aggregates_quantities_across_orders(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    p1 = Product(store_id=store_id, ozon_sku="111", name="Товар 1")
    p2 = Product(store_id=store_id, ozon_sku="222", name="Товар 2")
    db_session.add_all([p1, p2])
    db_session.commit()

    client = _FakeClient(
        pages=[[1001, 1002]],
        bundles={
            1001: [{"sku": 111, "quantity": 5}, {"sku": 222, "quantity": 3}],
            1002: [{"sku": 111, "quantity": 7}],
        },
    )

    outcome = sync_supply_order_pending_quantities(db_session, store_id=store_id, client=client)
    db_session.commit()

    assert outcome.orders_fetched == 2
    assert outcome.orders_with_bundle == 2
    assert not outcome.errors
    db_session.refresh(p1)
    db_session.refresh(p2)
    assert p1.pending_supply_units == 12
    assert p2.pending_supply_units == 3


def test_sync_sets_zero_for_products_not_in_any_pending_order(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    p1 = Product(store_id=store_id, ozon_sku="111", name="Товар 1", pending_supply_units=99)
    db_session.add(p1)
    db_session.commit()

    client = _FakeClient(pages=[[]], bundles={})
    sync_supply_order_pending_quantities(db_session, store_id=store_id, client=client)
    db_session.commit()

    db_session.refresh(p1)
    assert p1.pending_supply_units == 0


def test_sync_paginates_using_last_id(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient(pages=[[1001], [1002]], bundles={1001: [], 1002: []})

    outcome = sync_supply_order_pending_quantities(db_session, store_id=store_id, client=client)

    assert outcome.orders_fetched == 2
    assert client.list_calls == 2


def test_sync_continues_past_a_single_order_error(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    p1 = Product(store_id=store_id, ozon_sku="111", name="Товар 1")
    db_session.add(p1)
    db_session.commit()

    class _PartiallyFailingClient(_FakeClient):
        def get_supply_order(self, order_id: int):
            if order_id == 1001:
                from app.services.ozon.exceptions import OzonAPIError

                raise OzonAPIError("boom")
            return super().get_supply_order(order_id)

    client = _PartiallyFailingClient(pages=[[1001, 1002]], bundles={1002: [{"sku": 111, "quantity": 4}]})

    outcome = sync_supply_order_pending_quantities(db_session, store_id=store_id, client=client)
    db_session.commit()

    assert outcome.orders_fetched == 2
    assert outcome.orders_with_bundle == 1
    assert any("1001" in e for e in outcome.errors)
    db_session.refresh(p1)
    assert p1.pending_supply_units == 4


def test_sync_surfaces_auth_error_as_hard_failure(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id

    outcome = sync_supply_order_pending_quantities(db_session, store_id=store_id, client=_RaisingListClient())

    assert outcome.hard_failure is True
    assert any("bad key" in e for e in outcome.errors)


def test_warehouse_stocks_sync_route_requires_manager_role(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    viewer = User(email="supply_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "supply_viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-warehouse-stocks")
    assert resp.status_code == 403
