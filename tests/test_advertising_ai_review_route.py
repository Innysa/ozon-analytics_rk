"""Route-level tests for the AI advertising-campaign review endpoints
(GET/POST .../advertising/ai-review) — role gating, store isolation, and
the end-to-end generate-then-list flow. Runs against the real DemoProvider
(AI_PROVIDER=demo, DEMO_MODE=true per tests/conftest.py's env defaults) —
no network call, deterministic output — rather than monkeypatching the AI
provider, since DemoProvider is itself a real, if non-network, AIProvider
implementation."""
from datetime import date

from tests.conftest import login


def _setup_campaign_with_daily_stats(db_session, store_id: str):
    from app.models.advertising_campaign import AdvertisingCampaign
    from app.models.advertising_daily_statistic import AdvertisingDailyStatistic

    db_session.add(
        AdvertisingCampaign(store_id=store_id, ozon_campaign_id="777", name="Кампания 777", state="CAMPAIGN_STATE_RUNNING")
    )
    db_session.add(
        AdvertisingDailyStatistic(
            store_id=store_id, ozon_campaign_id="777", ozon_sku="123", date=date(2026, 8, 1),
            spend_rub=100, impressions=1000, clicks=50, source="ozon_performance_api",
        )
    )
    db_session.commit()


def test_generate_requires_manager_role(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    _setup_campaign_with_daily_stats(db_session, d["store_a"].id)
    viewer = User(email="ai_review_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "ai_review_viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/advertising/ai-review/generate")
    assert resp.status_code == 403


def test_generate_without_data_returns_400_with_a_clear_message(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/advertising/ai-review/generate")
    assert resp.status_code == 400
    assert "автосбор статистики рекламы" in resp.json()["detail"]


def test_generate_then_list_end_to_end(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    _setup_campaign_with_daily_stats(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")

    gen_resp = client.post(
        f"/api/stores/{d['store_a'].id}/advertising/ai-review/generate?date_from=2026-08-01&date_to=2026-08-01"
    )
    assert gen_resp.status_code == 200, gen_resp.text
    body = gen_resp.json()
    assert body["campaigns_analyzed"] == 1
    assert "ДЕМО" in body["overview"]
    assert body["insights"][0]["ozon_campaign_id"] == "777"

    list_resp = client.get(f"/api/stores/{d['store_a'].id}/advertising/ai-review")
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == body["id"]


def test_list_is_isolated_per_store(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    _setup_campaign_with_daily_stats(db_session, d["store_a"].id)
    login(client, "owner_a@example.com", "password123")
    client.post(f"/api/stores/{d['store_a'].id}/advertising/ai-review/generate?date_from=2026-08-01&date_to=2026-08-01")

    login(client, "owner_b@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_b'].id}/advertising/ai-review")
    assert resp.status_code == 200
    assert resp.json()["items"] == []
