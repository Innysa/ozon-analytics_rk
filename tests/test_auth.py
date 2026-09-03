from tests.conftest import login


def test_login_wrong_password_rejected(client, two_stores_with_users):
    resp = client.post("/api/auth/login", json={"email": "owner_a@example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_login_sets_httponly_cookie_not_json_token(client, two_stores_with_users):
    resp = client.post("/api/auth/login", json={"email": "owner_a@example.com", "password": "password123"})
    assert resp.status_code == 200
    assert "session_token" not in resp.text  # token must not appear in the JSON body
    set_cookie = resp.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie


def test_me_requires_session(client, two_stores_with_users):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401

    login(client, "owner_a@example.com", "password123")
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "owner_a@example.com"


def test_logout_clears_session(client, two_stores_with_users):
    login(client, "owner_a@example.com", "password123")
    assert client.get("/api/auth/me").status_code == 200

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_non_admin_cannot_create_users(client, two_stores_with_users):
    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        "/api/users", json={"email": "new@example.com", "full_name": "New", "password": "somepassword"}
    )
    assert resp.status_code == 403


def test_admin_can_create_user_and_assign_membership(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "admin@example.com", "adminpass123")

    resp = client.post(
        "/api/users", json={"email": "manager@example.com", "full_name": "Manager", "password": "somepassword"}
    )
    assert resp.status_code == 201
    new_user_id = resp.json()["id"]

    resp = client.post(
        "/api/users/memberships",
        json={"user_id": new_user_id, "store_id": d["store_a"].id, "role": "manager"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "manager"
