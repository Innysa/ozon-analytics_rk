"""Setting Ozon API credentials (Seller API and Performance API) is restricted
to the platform admin account (see require_platform_admin_for_store) — a
store's own "owner" role is no longer enough, even though it still suffices
for viewing the (masked) credential status and testing the connection.
Performance API coverage lives in test_advertising.py; this file covers the
Seller API side and confirms viewing is unaffected."""
from app.core.security import hash_password
from app.models.membership import StoreMembership, StoreRole
from app.models.user import User
from tests.conftest import login


def test_owner_cannot_set_seller_credentials(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.put(
        f"/api/stores/{d['store_a'].id}/ozon/credentials",
        json={"client_id": "some-id", "api_key": "some-key"},
    )
    assert resp.status_code == 403


def test_manager_cannot_set_seller_credentials(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    manager = User(email="seller_manager@example.com", full_name="Manager", password_hash=hash_password("password123"))
    db_session.add(manager)
    db_session.flush()
    db_session.add(StoreMembership(user_id=manager.id, store_id=d["store_a"].id, role=StoreRole.MANAGER))
    db_session.commit()

    login(client, "seller_manager@example.com", "password123")
    resp = client.put(
        f"/api/stores/{d['store_a'].id}/ozon/credentials",
        json={"client_id": "some-id", "api_key": "some-key"},
    )
    assert resp.status_code == 403


def test_admin_can_set_seller_credentials(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "admin@example.com", "adminpass123")

    resp = client.put(
        f"/api/stores/{d['store_a'].id}/ozon/credentials",
        json={"client_id": "some-id", "api_key": "some-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["configured"] is True


def test_owner_can_still_view_seller_credentials_status(client, two_stores_with_users):
    """Restricting who can SET the keys must not also block the store owner
    from seeing whether keys are configured — that view stays as before."""
    d = two_stores_with_users
    login(client, "admin@example.com", "adminpass123")
    client.put(
        f"/api/stores/{d['store_a'].id}/ozon/credentials",
        json={"client_id": "some-id", "api_key": "some-key"},
    )

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/ozon/credentials")
    assert resp.status_code == 200
    assert resp.json()["configured"] is True
