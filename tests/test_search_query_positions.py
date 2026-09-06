"""Tests for the "Позиции в поиске" tab's comparison logic
(compute_search_query_positions): current vs. previous position for each
(SKU, query) pair, built entirely from existing SearchQueryStatistic rows —
no separate table, since every upload already adds a new period_end
snapshot instead of overwriting (see app.schemas.search_query.
SearchQueryPositionOut's docstring)."""
from datetime import date

import pytest

from tests.conftest import login


def _add_snapshot(
    db_session,
    *,
    store_id: str,
    product_id: str | None,
    sku: str,
    query: str,
    position: float | None,
    period_start: date,
    period_end: date,
    people_searched: int = 100,
    people_saw: int = 50,
    orders: int = 1,
):
    from app.models.search_query_statistic import SearchQueryStatistic

    row = SearchQueryStatistic(
        store_id=store_id,
        product_id=product_id,
        ozon_sku=sku,
        query_text=query,
        period_start=period_start,
        period_end=period_end,
        position_ozon=position,
        people_searched=people_searched,
        people_saw=people_saw,
        ordered_units_by_query=orders,
        source="xlsx_import",
    )
    db_session.add(row)
    return row


def _make_product(db_session, store_id: str, sku: str):
    from app.models.product import Product

    product = Product(store_id=store_id, ozon_sku=sku, name=f"Товар {sku}")
    db_session.add(product)
    db_session.flush()
    return product


def test_no_previous_snapshot_reports_no_comparison(db_session, two_stores_with_users):
    from app.services.search_query_analytics_service import compute_search_query_positions

    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id, "111")
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=80, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
    )
    db_session.flush()

    result = compute_search_query_positions(db_session, store_id=d["store_a"].id)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.position == 80
    assert item.previous_position is None
    assert item.position_change is None
    assert item.position_change_direction is None


def test_position_improved_shows_up_direction(db_session, two_stores_with_users):
    """Position number DECREASING means the product moved UP in search
    results — must show direction="up" (green), not "down"."""
    from app.services.search_query_analytics_service import compute_search_query_positions

    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id, "111")
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=97, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
    )
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=81, period_start=date(2026, 9, 1), period_end=date(2026, 9, 6),
    )
    db_session.flush()

    result = compute_search_query_positions(db_session, store_id=d["store_a"].id)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.report_date == date(2026, 9, 6)
    assert item.position == 81
    assert item.previous_position == 97
    assert item.previous_report_date == date(2026, 8, 31)
    assert item.position_change == pytest.approx(16)  # 97 - 81
    assert item.position_change_direction == "up"


def test_position_worsened_shows_down_direction(db_session, two_stores_with_users):
    from app.services.search_query_analytics_service import compute_search_query_positions

    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id, "111")
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=50, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
    )
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=70, period_start=date(2026, 9, 1), period_end=date(2026, 9, 6),
    )
    db_session.flush()

    result = compute_search_query_positions(db_session, store_id=d["store_a"].id)
    item = result.items[0]
    assert item.position_change == pytest.approx(-20)
    assert item.position_change_direction == "down"


def test_unchanged_position_reports_no_direction(db_session, two_stores_with_users):
    from app.services.search_query_analytics_service import compute_search_query_positions

    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id, "111")
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=60, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
    )
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=60, period_start=date(2026, 9, 1), period_end=date(2026, 9, 6),
    )
    db_session.flush()

    result = compute_search_query_positions(db_session, store_id=d["store_a"].id)
    item = result.items[0]
    assert item.position_change == 0
    assert item.position_change_direction is None


def test_uses_most_recent_two_of_three_snapshots(db_session, two_stores_with_users):
    from app.services.search_query_analytics_service import compute_search_query_positions

    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id, "111")
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=120, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31),
    )
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=97, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
    )
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=81, period_start=date(2026, 9, 1), period_end=date(2026, 9, 6),
    )
    db_session.flush()

    result = compute_search_query_positions(db_session, store_id=d["store_a"].id)
    item = result.items[0]
    assert item.position == 81
    assert item.previous_position == 97  # not 120 — the second-most-recent, not the oldest


def test_query_text_filter_does_not_break_the_comparison(db_session, two_stores_with_users):
    """Filtering by query_text must only narrow the OUTPUT — it must not
    prevent an earlier snapshot of a matching query from being used as the
    comparison baseline."""
    from app.services.search_query_analytics_service import compute_search_query_positions

    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id, "111")
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=97, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
    )
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="обувница",
        position=81, period_start=date(2026, 9, 1), period_end=date(2026, 9, 6),
    )
    _add_snapshot(
        db_session, store_id=d["store_a"].id, product_id=product.id, sku="111", query="полка для обуви",
        position=40, period_start=date(2026, 9, 1), period_end=date(2026, 9, 6),
    )
    db_session.flush()

    result = compute_search_query_positions(db_session, store_id=d["store_a"].id, query_text="обувница")
    assert len(result.items) == 1
    assert result.items[0].query_text == "обувница"
    assert result.items[0].previous_position == 97


def test_store_isolation(db_session, two_stores_with_users):
    from app.services.search_query_analytics_service import compute_search_query_positions

    d = two_stores_with_users
    product_b = _make_product(db_session, d["store_b"].id, "999")
    _add_snapshot(
        db_session, store_id=d["store_b"].id, product_id=product_b.id, sku="999", query="чужой запрос",
        position=10, period_start=date(2026, 9, 1), period_end=date(2026, 9, 6),
    )
    db_session.flush()

    result = compute_search_query_positions(db_session, store_id=d["store_a"].id)
    assert result.items == []


def test_positions_via_api_end_to_end(client, db_session, two_stores_with_users):
    from app.models.product import Product
    from app.models.search_query_statistic import SearchQueryStatistic

    d = two_stores_with_users
    product = Product(store_id=d["store_a"].id, ozon_sku="111", name="Обувница")
    db_session.add(product)
    db_session.flush()
    db_session.add_all(
        [
            SearchQueryStatistic(
                store_id=d["store_a"].id, product_id=product.id, ozon_sku="111", query_text="обувница",
                period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
                position_ozon=97, people_searched=100, people_saw=50, ordered_units_by_query=1,
                source="xlsx_import",
            ),
            SearchQueryStatistic(
                store_id=d["store_a"].id, product_id=product.id, ozon_sku="111", query_text="обувница",
                period_start=date(2026, 9, 1), period_end=date(2026, 9, 6),
                position_ozon=81, people_searched=110, people_saw=60, ordered_units_by_query=2,
                source="xlsx_import",
            ),
        ]
    )
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/search-queries/positions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["position"] == 81
    assert item["previous_position"] == 97
    assert item["position_change_direction"] == "up"

    forbidden = client.get(f"/api/stores/{d['store_b'].id}/search-queries/positions")
    assert forbidden.status_code == 403
