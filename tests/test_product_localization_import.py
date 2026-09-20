"""Tests for app.services.product_localization_import — Ozon's own
«Планирование поставок → Локальность продаж» XLSX export (per SKU per
cluster), collapsed here into one weighted-average % per SKU on
Product.localization_pct. See that module's own docstring for the full
story of why this manual import exists at all (no confirmed API method)."""
import io

import openpyxl
import pytest

from app.models.product import Product
from app.services.product_localization_import import import_product_localization_from_file
from tests.conftest import login


def _build_xlsx(rows: list[dict]) -> bytes:
    """Builds a real XLSX matching the confirmed real-export layout: a
    metadata "Период: X - Y" line, a blank line, a single header row, then
    one row per (SKU, Кластер)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Период: 23.08.2026 - 19.09.2026"])
    ws.append(["Дата обновления: 20.09.2026 17:37 МСК (GMT+3)"])
    ws.append([])
    ws.append([
        "SKU", "Артикул", "Название товара", "Рекомендуемая поставка, шт на 56 дней",
        "Рекомендация", "Кластер", "Схема продаж", "Дней без остатка за 28 дней",
        "Доля локальных продаж", "Среднесуточные продажи, руб. за 28дн", "Признак товара",
        "До конца остатка FBO, дн", "До конца остатка FBS, дн", "Остаток FBO, шт", "Остаток FBS, шт",
        "Товары в пути на склад озон, шт", "Среднесуточные продажи, шт. за 28дн",
    ])
    for r in rows:
        ws.append([
            r["sku"], r.get("offer_id", "art"), r.get("name", "Товар"), 10, "Срочно поставить",
            r.get("cluster", "Москва"), "FBO, FBS", 5, r["share"], 1000, None, 10, 10, 5, 5, 0,
            r["weight"],
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _seed_product(db_session, store_id, sku, name="Товар"):
    product = Product(store_id=store_id, ozon_sku=sku, name=name)
    db_session.add(product)
    db_session.commit()
    return product


def test_weighted_average_across_clusters(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, "111")

    content = _build_xlsx([
        {"sku": 111, "cluster": "Москва", "share": 100, "weight": 3.0},
        {"sku": 111, "cluster": "Ростов", "share": 0, "weight": 1.0},
    ])

    result = import_product_localization_from_file(db_session, store_id=store_id, filename="loc.xlsx", content=content)
    db_session.commit()

    assert result.created == 1
    assert not result.errors
    product = db_session.query(Product).filter(Product.store_id == store_id, Product.ozon_sku == "111").first()
    # (100*3 + 0*1) / (3+1) = 75
    assert float(product.localization_pct) == 75.0
    assert str(product.localization_period_end) == "2026-09-19"


def test_zero_weight_rows_excluded_from_average(db_session, two_stores_with_users):
    """A cluster with zero measurable sales must not silently drag the
    weighted average toward its (often equally-zero, but not always
    meaningful) share value — see the module's own docstring."""
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, "111")

    content = _build_xlsx([
        {"sku": 111, "cluster": "Москва", "share": 80, "weight": 2.0},
        {"sku": 111, "cluster": "Ростов", "share": 0, "weight": 0},
    ])

    result = import_product_localization_from_file(db_session, store_id=store_id, filename="loc.xlsx", content=content)
    db_session.commit()

    assert result.created == 1
    product = db_session.query(Product).filter(Product.store_id == store_id, Product.ozon_sku == "111").first()
    assert float(product.localization_pct) == 80.0


def test_sku_not_in_catalog_reported_not_crashed(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    # Deliberately no Product seeded for SKU 999.

    content = _build_xlsx([{"sku": 999, "share": 50, "weight": 1.0}])

    result = import_product_localization_from_file(db_session, store_id=store_id, filename="loc.xlsx", content=content)

    assert result.created == 0
    assert any("999" in e for e in result.errors)


def test_missing_required_columns_reports_error(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Период: 23.08.2026 - 19.09.2026"])
    ws.append([])
    ws.append(["SKU", "Кластер", "Что-то ещё"])
    ws.append([111, "Москва", 5])
    buf = io.BytesIO()
    wb.save(buf)

    result = import_product_localization_from_file(db_session, store_id=store_id, filename="loc.xlsx", content=buf.getvalue())

    assert result.created == 0
    assert any("отсутствуют обязательные колонки" in e for e in result.errors)


def test_non_xlsx_extension_rejected(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    result = import_product_localization_from_file(db_session, store_id=store_id, filename="loc.csv", content=b"whatever")
    assert result.created == 0
    assert any(".xlsx" in e for e in result.errors)


def test_reupload_overwrites_previous_value(db_session, two_stores_with_users):
    """Same convention as cost_price_rub — a fresh upload REPLACES the
    stored value, not accumulates it."""
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, "111")

    first = _build_xlsx([{"sku": 111, "share": 20, "weight": 1.0}])
    import_product_localization_from_file(db_session, store_id=store_id, filename="loc.xlsx", content=first)
    db_session.commit()

    second = _build_xlsx([{"sku": 111, "share": 90, "weight": 1.0}])
    import_product_localization_from_file(db_session, store_id=store_id, filename="loc.xlsx", content=second)
    db_session.commit()

    product = db_session.query(Product).filter(Product.store_id == store_id, Product.ozon_sku == "111").first()
    assert float(product.localization_pct) == 90.0


def test_upload_route_end_to_end(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, "111")
    content = _build_xlsx([{"sku": 111, "share": 60, "weight": 2.0}])

    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        f"/api/stores/{store_id}/products/upload-localization",
        files={"file": ("loc.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 1
    assert body["errors"] == []

    resp2 = client.get(f"/api/stores/{store_id}/products/{_product_id(db_session, store_id, '111')}")
    assert resp2.status_code == 200
    assert float(resp2.json()["localization_pct"]) == 60.0


def _product_id(db_session, store_id, sku) -> str:
    return db_session.query(Product).filter(Product.store_id == store_id, Product.ozon_sku == sku).first().id


def test_upload_route_requires_manager_role(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    viewer = User(email="loc_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    content = _build_xlsx([{"sku": 111, "share": 60, "weight": 2.0}])
    login(client, "loc_viewer@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/products/upload-localization",
        files={"file": ("loc.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 403
