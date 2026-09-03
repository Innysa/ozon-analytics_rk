"""Tests for the advertising (Ozon Performance API) architecture: store
isolation on the new endpoints, owner-only credential management, and
defensive parsing of Ozon's campaign payload (never fabricate data for
fields Ozon didn't actually send)."""
from tests.conftest import login


def test_owner_cannot_read_other_store_performance_credentials(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.get(f"/api/stores/{d['store_b'].id}/ozon/performance/credentials")
    assert resp.status_code == 403


def test_owner_cannot_read_other_store_campaigns(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.get(f"/api/stores/{d['store_b'].id}/advertising/campaigns")
    assert resp.status_code == 403


def test_manager_cannot_set_performance_credentials(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    manager = User(email="perf_manager@example.com", full_name="Manager", password_hash=hash_password("password123"))
    db_session.add(manager)
    db_session.flush()
    db_session.add(StoreMembership(user_id=manager.id, store_id=d["store_a"].id, role=StoreRole.MANAGER))
    db_session.commit()

    login(client, "perf_manager@example.com", "password123")
    resp = client.put(
        f"/api/stores/{d['store_a'].id}/ozon/performance/credentials",
        json={"client_id": "some-id", "client_secret": "some-secret"},
    )
    assert resp.status_code == 403


def test_owner_can_set_and_never_see_full_performance_secret(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.put(
        f"/api/stores/{d['store_a'].id}/ozon/performance/credentials",
        json={"client_id": "12345678", "client_secret": "top-secret-value"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert "top-secret-value" not in resp.text
    assert body["client_secret_masked"] != "top-secret-value"


def test_seller_credentials_unaffected_by_performance_only_setup(client, two_stores_with_users):
    """Setting only Performance credentials must not make the Seller API
    credentials endpoint incorrectly report itself as configured."""
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    client.put(
        f"/api/stores/{d['store_a'].id}/ozon/performance/credentials",
        json={"client_id": "perf-id", "client_secret": "perf-secret"},
    )

    resp = client.get(f"/api/stores/{d['store_a'].id}/ozon/credentials")
    assert resp.status_code == 200
    assert resp.json()["configured"] is False


def test_campaigns_list_empty_by_default_no_fabricated_data(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.get(f"/api/stores/{d['store_a'].id}/advertising/campaigns")
    assert resp.status_code == 200
    assert resp.json() == []


def test_campaign_item_parses_defensively_with_extra_unknown_fields():
    """Ozon may add fields we don't know about yet; the schema must not
    reject the payload, and daily_budget must never be guessed if absent."""
    from app.services.ozon_performance.schemas import OzonCampaignItem

    item = OzonCampaignItem.model_validate(
        {
            "id": "999",
            "title": "Тестовая кампания",
            "state": "CAMPAIGN_STATE_RUNNING",
            "advObjectType": "SKU",
            "someBrandNewFieldOzonAddedLater": {"nested": True},
        }
    )
    assert item.title == "Тестовая кампания"
    assert item.daily_budget_rub is None  # not fabricated when absent


def test_campaign_item_tolerates_non_numeric_budget():
    from app.services.ozon_performance.schemas import OzonCampaignItem

    item = OzonCampaignItem.model_validate({"id": "1", "dailyBudget": "not-a-number"})
    assert item.daily_budget_rub is None


def test_token_response_requires_access_token():
    import pytest
    from pydantic import ValidationError

    from app.services.ozon_performance.schemas import OzonTokenResponse

    with pytest.raises(ValidationError):
        OzonTokenResponse.model_validate({"token_type": "Bearer"})
