"""Tests for the "Результаты по запросу" competitor-preview parser
(app.services.search_query_competitors_service) — a stateless preview,
never written to the database, deliberately separate from the position
history/highlighting feature in test_search_query_positions.py.

Uses an anonymized fixture with the SAME real structure as an actual Ozon
export (metadata block, single flat header row, a blank row, then an
explanatory-tooltip row with long descriptive text and no numeric
"Позиция" — both must be skipped — verified against the real file before
writing tests/fixtures/ozon_query_competitors_sample.xlsx; the real file is
not committed since it names a real product/query/competitors)."""
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import login

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ozon_query_competitors_sample.xlsx"


@pytest.fixture()
def report_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def test_parses_metadata_block(report_bytes):
    from app.services.search_query_competitors_service import parse_query_competitors_report

    report = parse_query_competitors_report(filename="report.xlsx", content=report_bytes)
    assert report.query_text == "тестовый запрос"
    assert report.region == "г. Москва, Россия"
    assert report.generated_at == date(2026, 9, 1)
    assert report.positions_in_results == 3


def test_skips_blank_and_explanatory_rows_keeps_three_data_rows(report_bytes):
    from app.services.search_query_competitors_service import parse_query_competitors_report

    report = parse_query_competitors_report(filename="report.xlsx", content=report_bytes)
    assert len(report.rows) == 3
    assert [r.position for r in report.rows] == [1, 2, 3]


def test_parses_row_fields_including_missing_value_markers(report_bytes):
    from app.services.search_query_competitors_service import parse_query_competitors_report

    report = parse_query_competitors_report(filename="report.xlsx", content=report_bytes)
    row1 = report.rows[0]
    assert row1.ozon_product_id == 1000001
    assert row1.product_name == "Тестовый товар А"
    assert row1.seller_name == "Тестовый продавец 1"
    assert row1.overall_score == pytest.approx(1.0)
    assert row1.cpc_bid_rub == pytest.approx(5.0)
    assert row1.cpo_bid_text is None  # "—" is a missing-value marker, not literal text
    assert row1.price_rub == pytest.approx(1000.0)
    assert row1.price_index_pct == pytest.approx(10.0)

    row2 = report.rows[1]
    assert row2.cpo_bid_text == "10%"
    assert row2.reviews_text is None  # "— " (with trailing space) also treated as missing


def test_missing_query_in_header_raises_value_error():
    from app.services.search_query_competitors_service import parse_query_competitors_report

    with pytest.raises(ValueError, match="запрос"):
        parse_query_competitors_report(filename="bad.csv", content=b"just,some,csv\n1,2,3\n")


def test_preview_via_api_requires_manager_role(client, db_session, two_stores_with_users, report_bytes):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    viewer = User(email="competitors_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    login(client, "competitors_viewer@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/search-queries/competitors/preview",
        files={"file": ("report.xlsx", report_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 403


def test_preview_via_api_end_to_end(client, db_session, two_stores_with_users, report_bytes):
    from app.models.search_query_statistic import SearchQueryStatistic

    d = two_stores_with_users
    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/search-queries/competitors/preview",
        files={"file": ("report.xlsx", report_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query_text"] == "тестовый запрос"
    assert len(body["rows"]) == 3

    # Stateless preview — nothing gets written to the database.
    count = db_session.query(SearchQueryStatistic).filter(SearchQueryStatistic.store_id == d["store_a"].id).count()
    assert count == 0
