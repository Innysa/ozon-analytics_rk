"""Route-level tests for the automatic advertising-statistics sync: manual
trigger (POST .../sync/ozon-advertising-statistics) and the listing endpoint
(GET .../advertising/daily-statistics) — role gating and store isolation,
matching every other sync endpoint's test conventions. The Performance API
client itself is monkeypatched out (a fake, duck-typed stand-in, same
approach as test_advertising_daily_sync_service.py) since this is a route
test, not a client/HTTP test."""
import io
import zipfile
from datetime import date

from app.core.encryption import encrypt_secret
from app.services.ozon_performance.schemas import OzonStatisticsReportStatus
from tests.conftest import login

_HEADER = (
    "SKU;Название товара;День;Цена товара;Тип страницы;Условие показа;Показы;Клики;"
    "CTR (%);В корзину;Средняя ставка (руб.);Расход, ₽ с НДС;Заказы;Выручка, ₽;"
    "Заказы модели;Выручка с заказов модели, ₽"
)


def _zip_for_campaign(ozon_campaign_id: str, day: str = "01.09.2026") -> bytes:
    buf = io.BytesIO()
    row = f"777;Товар 777;{day};100;PDP;search;500;25;5,0;3;2,50;62,50;2;900,00;0;0"
    csv_text = "\n".join(["Статистика; период", _HEADER, row])
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{ozon_campaign_id}_{day}-{day}.csv", csv_text.encode("utf-8-sig"))
    return buf.getvalue()


class _FakePerformanceClient:
    """Stands in for OzonPerformanceClient as a context manager, matching
    the real class's `with OzonPerformanceClient(...) as client:` usage in
    app.api.routes.sync."""

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def create_statistics_report(self, campaign_ids, date_from, date_to):
        return "uuid:" + ",".join(campaign_ids)

    def get_statistics_report_status(self, uuid: str) -> OzonStatisticsReportStatus:
        return OzonStatisticsReportStatus(state="OK", link=uuid)

    def download_statistics_report(self, link: str) -> bytes:
        campaign_ids = link.replace("uuid:", "").split(",")
        return _zip_for_campaign(campaign_ids[0])


class _NoCloseSessionWrapper:
    """The manual-trigger route runs the actual sync in a FastAPI
    BackgroundTask using its own SessionLocal() (a fresh connection — see
    app.api.routes.sync's docstring on why), which in production is correct
    but in this test suite would be invisible to the test's own db_session
    (each test runs inside an uncommitted outer transaction on its own
    connection, per tests/conftest.py). Wrapping the test's db_session and
    patching SessionLocal to return it keeps everything on one connection so
    the test can see what the "background" sync actually wrote — while
    swallowing close() so the background task doesn't tear down the
    fixture's session out from under the rest of the test."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self) -> None:
        pass


def _setup_store_with_performance_creds(db_session, store_id: str):
    from app.models.advertising_campaign import AdvertisingCampaign
    from app.models.ozon_credentials import OzonCredentials

    creds = OzonCredentials(
        store_id=store_id,
        performance_client_id_encrypted=encrypt_secret("perf-client-id"),
        performance_client_secret_encrypted=encrypt_secret("perf-client-secret"),
    )
    db_session.add(creds)
    db_session.add(
        AdvertisingCampaign(store_id=store_id, ozon_campaign_id="555", name="Кампания 555", state="CAMPAIGN_STATE_RUNNING")
    )
    db_session.commit()


def test_sync_requires_manager_role(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    _setup_store_with_performance_creds(db_session, d["store_a"].id)
    viewer = User(email="perf_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "perf_viewer@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-advertising-statistics")
    assert resp.status_code == 403


def test_sync_without_performance_credentials_returns_400(client, two_stores_with_users):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-advertising-statistics")
    assert resp.status_code == 400


def test_sync_end_to_end_creates_daily_statistics_and_syncrun(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes

    monkeypatch.setattr(sync_routes, "OzonPerformanceClient", _FakePerformanceClient)
    monkeypatch.setattr(sync_routes, "SessionLocal", lambda: _NoCloseSessionWrapper(db_session))

    d = two_stores_with_users
    _setup_store_with_performance_creds(db_session, d["store_a"].id)

    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/sync/ozon-advertising-statistics",
        params={"date_from": "2026-09-01", "date_to": "2026-09-01"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_type"] == "ozon_advertising_statistics_api"
    # The endpoint returns immediately (the sync runs in a BackgroundTask and
    # can take minutes on a real account), so the response itself still shows
    # "running" — the frontend polls GET /runs for the final outcome, same as
    # every other sync endpoint in this app.
    assert body["status"] == "running"

    runs = client.get(f"/api/stores/{d['store_a'].id}/sync/runs")
    assert runs.status_code == 200
    finished_run = next(r for r in runs.json() if r["id"] == body["id"])
    assert finished_run["status"] in ("success", "partial")
    assert finished_run["items_created"] == 1

    listing = client.get(f"/api/stores/{d['store_a'].id}/advertising/daily-statistics")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["ozon_sku"] == "777"
    assert items[0]["ozon_campaign_id"] == "555"
    assert items[0]["date"] == "2026-09-01"
    assert items[0]["impressions"] == 500
    assert items[0]["spend_rub"] == 62.50


def test_store_isolation_on_daily_statistics_listing(client, db_session, two_stores_with_users, monkeypatch):
    import app.api.routes.sync as sync_routes

    monkeypatch.setattr(sync_routes, "OzonPerformanceClient", _FakePerformanceClient)
    monkeypatch.setattr(sync_routes, "SessionLocal", lambda: _NoCloseSessionWrapper(db_session))

    d = two_stores_with_users
    _setup_store_with_performance_creds(db_session, d["store_a"].id)

    login(client, "owner_a@example.com", "password123")
    client.post(
        f"/api/stores/{d['store_a'].id}/sync/ozon-advertising-statistics",
        params={"date_from": "2026-09-01", "date_to": "2026-09-01"},
    )

    forbidden = client.get(f"/api/stores/{d['store_b'].id}/advertising/daily-statistics")
    assert forbidden.status_code == 403

    login(client, "owner_b@example.com", "password123")
    empty = client.get(f"/api/stores/{d['store_b'].id}/advertising/daily-statistics")
    assert empty.status_code == 200
    assert empty.json()["items"] == []
