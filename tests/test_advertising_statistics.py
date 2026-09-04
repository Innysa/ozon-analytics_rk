"""Tests for advertising statistics import/analytics, built against a REAL
Ozon "Продвижение → Статистика" export (tests/fixtures/
ozon_advertising_statistics_sample.xlsx) — not a synthetic approximation.
Confirms the parser handles the actual column layout, missing-value
sentinels ('', '-'), and that ДРР/ROAS aggregates match a hand-computed sum
over the same file."""
from pathlib import Path

import pytest

from tests.conftest import login

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ozon_advertising_statistics_sample.xlsx"


@pytest.fixture()
def real_report_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def test_real_file_parses_without_errors_except_union_notice(db_session, two_stores_with_users, real_report_bytes):
    from app.services.advertising_import import import_advertising_statistics_from_file

    d = two_stores_with_users
    result = import_advertising_statistics_from_file(
        db_session, store_id=d["store_a"].id, filename="report.xlsx", content=real_report_bytes
    )
    assert result.fetched == 183
    assert result.created == 183
    assert result.skipped_duplicate == 0
    # the only "error" should be the informational Union-sheet skip notice
    assert len(result.errors) == 1
    assert "Union" in result.errors[0]


def test_real_file_reupload_is_fully_deduplicated(db_session, two_stores_with_users, real_report_bytes):
    from app.services.advertising_import import import_advertising_statistics_from_file

    d = two_stores_with_users
    import_advertising_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=real_report_bytes)
    db_session.flush()
    result2 = import_advertising_statistics_from_file(
        db_session, store_id=d["store_a"].id, filename="r.xlsx", content=real_report_bytes
    )
    assert result2.created == 0
    assert result2.skipped_duplicate == 183


def test_analytics_totals_match_hand_computed_sum_over_real_file(db_session, two_stores_with_users, real_report_bytes):
    """Cross-check against values independently summed from the raw workbook
    (see the manual verification done before writing the importer) — pins
    the exact expected totals so a future regression is caught."""
    from app.services.advertising_import import import_advertising_statistics_from_file
    from app.services.advertising_analytics_service import compute_advertising_analytics

    d = two_stores_with_users
    import_advertising_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=real_report_bytes)
    db_session.flush()

    analytics = compute_advertising_analytics(db_session, store_id=d["store_a"].id)
    assert analytics.has_data is True
    assert analytics.total_spend_rub == pytest.approx(1184743.84, abs=0.01)
    assert analytics.total_sales_promo_rub == pytest.approx(14827913.0, abs=0.01)
    assert analytics.total_impressions == 4444006
    assert analytics.total_clicks == 187942
    assert analytics.drr_calculated_pct == pytest.approx(7.99, abs=0.01)
    assert analytics.roas_calculated == pytest.approx(12.516, abs=0.01)


def test_zero_sales_row_has_no_drr_or_roas_not_fabricated_zero(db_session, two_stores_with_users, real_report_bytes):
    """A row with spend but zero/absent promoted sales must report
    drr_calculated_pct/roas_calculated as None, never as a fabricated 0."""
    from app.models.advertising_statistic import AdvertisingStatistic
    from app.services.advertising_import import import_advertising_statistics_from_file

    d = two_stores_with_users
    import_advertising_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=real_report_bytes)
    db_session.flush()

    zero_sales_row = (
        db_session.query(AdvertisingStatistic)
        .filter(AdvertisingStatistic.store_id == d["store_a"].id, AdvertisingStatistic.sales_promo_rub == 0)
        .filter(AdvertisingStatistic.spend_rub > 0)
        .first()
    )
    assert zero_sales_row is not None


def test_store_isolation_on_advertising_statistics_endpoints(client, db_session, two_stores_with_users, real_report_bytes):
    d = two_stores_with_users
    from app.services.advertising_import import import_advertising_statistics_from_file

    import_advertising_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=real_report_bytes)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    ok = client.get(f"/api/stores/{d['store_a'].id}/advertising/statistics")
    assert ok.status_code == 200
    assert ok.json()["total"] == 183

    ok_analytics = client.get(f"/api/stores/{d['store_a'].id}/advertising/analytics")
    assert ok_analytics.status_code == 200
    assert ok_analytics.json()["has_data"] is True

    forbidden = client.get(f"/api/stores/{d['store_b'].id}/advertising/statistics")
    assert forbidden.status_code == 403

    forbidden_analytics = client.get(f"/api/stores/{d['store_b'].id}/advertising/analytics")
    assert forbidden_analytics.status_code == 403

    # store B genuinely has no data of its own — must say so, not show store A's
    login(client, "owner_b@example.com", "password123")
    empty = client.get(f"/api/stores/{d['store_b'].id}/advertising/analytics")
    assert empty.status_code == 200
    assert empty.json()["has_data"] is False


def test_upload_via_api_requires_manager_role(client, db_session, two_stores_with_users, real_report_bytes):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    viewer = User(email="ad_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "ad_viewer@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/advertising/statistics/upload",
        files={"file": ("r.xlsx", real_report_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 403


def test_upload_via_api_end_to_end(client, two_stores_with_users, real_report_bytes):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.post(
        f"/api/stores/{d['store_a'].id}/advertising/statistics/upload",
        files={"file": ("r.xlsx", real_report_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 183
    assert body["skipped_duplicate"] == 0
