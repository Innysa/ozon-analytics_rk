"""A real production incident: a store's Seller/Performance API keys were
displayed as configured (masked) but Ozon rejected them with "Неверный
Client-Id или Api-Key" on every connection check. The most common real-world
cause of exactly this symptom is a stray leading/trailing space or newline
picked up when copy-pasting the key out of Ozon's own cabinet — the app was
storing (and sending to Ozon) the value byte-for-byte, whitespace included.
OzonCredentialsIn/PerformanceCredentialsIn now strip whitespace before the
value is ever encrypted or sent anywhere."""
from tests.conftest import login


def test_seller_credentials_strip_surrounding_whitespace(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "admin@example.com", "adminpass123")

    resp = client.put(
        f"/api/stores/{d['store_a'].id}/ozon/credentials",
        json={"client_id": "  real-client-id\n", "api_key": "real-api-key \t"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # The masked value is derived from the stored (post-strip) plaintext —
    # its visible suffix must match the trimmed value, not the raw input's
    # trailing whitespace.
    assert body["client_id_masked"].endswith("-id")
    assert body["api_key_masked"].endswith("key")


def test_seller_credentials_reject_whitespace_only(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "admin@example.com", "adminpass123")

    resp = client.put(
        f"/api/stores/{d['store_a'].id}/ozon/credentials",
        json={"client_id": "   ", "api_key": "real-api-key"},
    )
    assert resp.status_code == 422


def test_performance_credentials_strip_surrounding_whitespace(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "admin@example.com", "adminpass123")

    resp = client.put(
        f"/api/stores/{d['store_a'].id}/ozon/performance/credentials",
        json={"client_id": " perf-client-id ", "client_secret": "\nperf-secret\n"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["client_id_masked"].endswith("-id")
    assert body["client_secret_masked"].endswith("ret")


def test_performance_credentials_reject_whitespace_only(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "admin@example.com", "adminpass123")

    resp = client.put(
        f"/api/stores/{d['store_a'].id}/ozon/performance/credentials",
        json={"client_id": "perf-client-id", "client_secret": "  \n  "},
    )
    assert resp.status_code == 422
