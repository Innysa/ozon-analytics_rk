"""Tests for product-card analytics import/aggregation, built against an
anonymized fixture with the SAME real header structure as an actual Ozon
"Аналитика → Товары" export (verified separately against the real file
before writing tests/fixtures/ozon_product_card_analytics_sample.xlsx —
the real file itself is not committed since it names a real seller)."""
from pathlib import Path

import pytest

from tests.conftest import login

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ozon_product_card_analytics_sample.xlsx"


@pytest.fixture()
def report_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def test_fixture_parses_two_daily_rows(db_session, two_stores_with_users, report_bytes):
    from app.services.product_analytics_import import import_product_card_statistics_from_file

    d = two_stores_with_users
    result = import_product_card_statistics_from_file(
        db_session, store_id=d["store_a"].id, filename="report.xlsx", content=report_bytes
    )
    assert result.fetched == 2
    assert result.created == 2
    assert result.skipped_duplicate == 0
    assert result.errors == []


def test_reupload_is_deduplicated_by_sku_and_date(db_session, two_stores_with_users, report_bytes):
    from app.services.product_analytics_import import import_product_card_statistics_from_file

    d = two_stores_with_users
    import_product_card_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=report_bytes)
    db_session.flush()
    result2 = import_product_card_statistics_from_file(
        db_session, store_id=d["store_a"].id, filename="r.xlsx", content=report_bytes
    )
    assert result2.created == 0
    assert result2.skipped_duplicate == 2


def test_totals_row_and_description_row_are_not_imported_as_data(db_session, two_stores_with_users, report_bytes):
    """The "Итого и среднее" aggregate row and the column-description row
    must both be skipped — only the 2 real daily rows should land in the DB."""
    from app.models.product_card_statistic import ProductCardStatistic
    from app.services.product_analytics_import import import_product_card_statistics_from_file

    d = two_stores_with_users
    import_product_card_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=report_bytes)
    db_session.flush()

    count = (
        db_session.query(ProductCardStatistic)
        .filter(ProductCardStatistic.store_id == d["store_a"].id)
        .count()
    )
    assert count == 2


def test_analytics_summary_sums_facts_and_computes_own_rates(db_session, two_stores_with_users, report_bytes):
    from app.services.product_analytics_import import import_product_card_statistics_from_file
    from app.services.product_analytics_service import compute_product_card_analytics

    d = two_stores_with_users
    import_product_card_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=report_bytes)
    db_session.flush()

    summary = compute_product_card_analytics(db_session, store_id=d["store_a"].id)
    assert summary.has_data is True
    assert summary.total_impressions == 5000  # 2500 + 2500
    assert summary.total_ordered_units == 10  # 5 + 5
    assert summary.total_bought_out_units == 9  # 4 + 5
    # calculated, not fabricated: bought_out/ordered = 9/10 = 90%
    assert summary.buyout_rate_calculated_pct == pytest.approx(90.0, abs=0.01)
    assert summary.latest_rating == pytest.approx(4.8, abs=0.01)
    assert summary.latest_stock == 195  # last day's value, not summed


def test_no_data_reports_has_data_false_not_zeros(db_session, two_stores_with_users):
    from app.services.product_analytics_service import compute_product_card_analytics

    d = two_stores_with_users
    summary = compute_product_card_analytics(db_session, store_id=d["store_a"].id)
    assert summary.has_data is False


def test_store_isolation_on_product_analytics_endpoints(client, db_session, two_stores_with_users, report_bytes):
    from app.services.product_analytics_import import import_product_card_statistics_from_file

    d = two_stores_with_users
    import_product_card_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=report_bytes)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    ok = client.get(f"/api/stores/{d['store_a'].id}/product-analytics")
    assert ok.status_code == 200
    assert ok.json()["total"] == 2

    ok_summary = client.get(f"/api/stores/{d['store_a'].id}/product-analytics/summary")
    assert ok_summary.status_code == 200
    assert ok_summary.json()["has_data"] is True

    forbidden = client.get(f"/api/stores/{d['store_b'].id}/product-analytics")
    assert forbidden.status_code == 403

    forbidden_summary = client.get(f"/api/stores/{d['store_b'].id}/product-analytics/summary")
    assert forbidden_summary.status_code == 403

    login(client, "owner_b@example.com", "password123")
    empty = client.get(f"/api/stores/{d['store_b'].id}/product-analytics/summary")
    assert empty.status_code == 200
    assert empty.json()["has_data"] is False


def test_upload_requires_manager_role(client, db_session, two_stores_with_users, report_bytes):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    viewer = User(email="pa_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "pa_viewer@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/product-analytics/upload",
        files={"file": ("r.xlsx", report_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 403


def test_upload_via_api_end_to_end(client, two_stores_with_users, report_bytes):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.post(
        f"/api/stores/{d['store_a'].id}/product-analytics/upload",
        files={"file": ("r.xlsx", report_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 2
    assert body["skipped_duplicate"] == 0


def test_tolerant_xlsx_loader_fixes_known_bad_casing():
    """Real Ozon-export-pipeline files were observed with horizontal="Left"
    (should be "left") and activePane="bottom-right" (should be
    "bottomRight") — openpyxl rejects these outright. Confirm the fix
    actually normalizes both, using minimal synthetic XML fragments."""
    from app.services.xlsx_compat import _fix_active_pane, _fix_style_casing

    assert _fix_style_casing('<alignment horizontal="Left" vertical="Top"/>') == '<alignment horizontal="left" vertical="top"/>'
    assert _fix_active_pane('<pane activePane="bottom-right" state="frozen"/>') == '<pane activePane="bottomRight" state="frozen"/>'
