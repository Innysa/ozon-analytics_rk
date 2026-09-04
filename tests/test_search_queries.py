"""Tests for search-query analytics import/aggregation, built against an
anonymized fixture with the SAME real structure as an actual Ozon
"Аналитика → Запросы" export — metadata block, single flat header row, and
the hierarchical product-header + query-detail block layout, with
Russian-formatted numbers (space thousands separators, comma decimals,
'%'/'₽' suffixes) — verified separately against the real file before
writing tests/fixtures/ozon_search_queries_sample.xlsx. Unlike the
product-card export, this real file has no seller-name field at all, but
the real file is still not committed (it names a real product/SKU)."""
from pathlib import Path

import pytest

from tests.conftest import login

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ozon_search_queries_sample.xlsx"


@pytest.fixture()
def report_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def test_fixture_parses_two_query_rows(db_session, two_stores_with_users, report_bytes):
    from app.services.search_query_import import import_search_query_statistics_from_file

    d = two_stores_with_users
    result = import_search_query_statistics_from_file(
        db_session, store_id=d["store_a"].id, filename="report.xlsx", content=report_bytes
    )
    assert result.fetched == 2
    assert result.created == 2
    assert result.skipped_duplicate == 0
    assert result.errors == []


def test_hierarchical_block_is_parsed_correctly(db_session, two_stores_with_users, report_bytes):
    """The product-header row (SKU/Артикул/Название filled, metrics blank)
    must not be imported as a data row, and both query-detail rows that
    follow it must carry the product identity forward correctly."""
    from app.models.search_query_statistic import SearchQueryStatistic
    from app.services.search_query_import import import_search_query_statistics_from_file

    d = two_stores_with_users
    import_search_query_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=report_bytes)
    db_session.flush()

    rows = (
        db_session.query(SearchQueryStatistic)
        .filter(SearchQueryStatistic.store_id == d["store_a"].id)
        .order_by(SearchQueryStatistic.query_text)
        .all()
    )
    assert len(rows) == 2
    assert {r.ozon_sku for r in rows} == {"1234567890"}
    assert {r.offer_id for r in rows} == {"test-art-1"}

    by_text = {r.query_text: r for r in rows}
    q1 = by_text["тестовый запрос"]
    assert q1.people_searched == 1234
    assert q1.people_saw == 567
    assert float(q1.position_ozon) == pytest.approx(12.0)
    assert float(q1.conv_search_to_card_pct_ozon) == pytest.approx(3.5)
    assert float(q1.conv_search_to_order_pct_ozon) == pytest.approx(0.2)
    assert q1.ordered_units_by_query == 5
    assert float(q1.ordered_sum_by_query_rub) == pytest.approx(12500.0)

    q2 = by_text["другой запрос"]
    assert q2.people_searched == 89
    assert q2.ordered_units_by_query == 0
    assert float(q2.ordered_sum_by_query_rub) == pytest.approx(0.0)


def test_reupload_is_deduplicated_by_sku_query_and_period(db_session, two_stores_with_users, report_bytes):
    from app.services.search_query_import import import_search_query_statistics_from_file

    d = two_stores_with_users
    import_search_query_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=report_bytes)
    db_session.flush()
    result2 = import_search_query_statistics_from_file(
        db_session, store_id=d["store_a"].id, filename="r.xlsx", content=report_bytes
    )
    assert result2.created == 0
    assert result2.skipped_duplicate == 2


def test_analytics_summary_sums_facts_and_computes_own_rates(db_session, two_stores_with_users, report_bytes):
    from app.services.search_query_analytics_service import compute_search_query_analytics
    from app.services.search_query_import import import_search_query_statistics_from_file

    d = two_stores_with_users
    import_search_query_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=report_bytes)
    db_session.flush()

    summary = compute_search_query_analytics(db_session, store_id=d["store_a"].id)
    assert summary.has_data is True
    assert summary.distinct_queries == 2
    assert summary.total_people_searched == 1323  # 1234 + 89
    assert summary.total_people_saw == 601  # 567 + 34
    assert summary.total_ordered_units == 5  # 5 + 0
    assert summary.total_ordered_sum_rub == pytest.approx(12500.0)
    # calculated, not fabricated: 5 ordered / 1323 searched * 100
    assert summary.order_rate_calculated_pct == pytest.approx(5 / 1323 * 100, abs=0.001)
    assert summary.avg_position_calculated == pytest.approx((12.0 + 45.0) / 2, abs=0.01)
    assert [q.query_text for q in summary.top_queries_by_searches] == ["тестовый запрос", "другой запрос"]


def test_no_data_reports_has_data_false_not_zeros(db_session, two_stores_with_users):
    from app.services.search_query_analytics_service import compute_search_query_analytics

    d = two_stores_with_users
    summary = compute_search_query_analytics(db_session, store_id=d["store_a"].id)
    assert summary.has_data is False


def test_store_isolation_on_search_query_endpoints(client, db_session, two_stores_with_users, report_bytes):
    from app.services.search_query_import import import_search_query_statistics_from_file

    d = two_stores_with_users
    import_search_query_statistics_from_file(db_session, store_id=d["store_a"].id, filename="r.xlsx", content=report_bytes)
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    ok = client.get(f"/api/stores/{d['store_a'].id}/search-queries")
    assert ok.status_code == 200
    assert ok.json()["total"] == 2

    ok_summary = client.get(f"/api/stores/{d['store_a'].id}/search-queries/summary")
    assert ok_summary.status_code == 200
    assert ok_summary.json()["has_data"] is True

    forbidden = client.get(f"/api/stores/{d['store_b'].id}/search-queries")
    assert forbidden.status_code == 403

    forbidden_summary = client.get(f"/api/stores/{d['store_b'].id}/search-queries/summary")
    assert forbidden_summary.status_code == 403

    login(client, "owner_b@example.com", "password123")
    empty = client.get(f"/api/stores/{d['store_b'].id}/search-queries/summary")
    assert empty.status_code == 200
    assert empty.json()["has_data"] is False


def test_upload_requires_manager_role(client, db_session, two_stores_with_users, report_bytes):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    viewer = User(email="sq_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "sq_viewer@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/search-queries/upload",
        files={"file": ("r.xlsx", report_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 403


def test_upload_via_api_end_to_end(client, two_stores_with_users, report_bytes):
    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")

    resp = client.post(
        f"/api/stores/{d['store_a'].id}/search-queries/upload",
        files={"file": ("r.xlsx", report_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 2
    assert body["skipped_duplicate"] == 0
