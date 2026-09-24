"""Tests for app.services.product_cost_price_import — bulk себестоимость
import from a seller-maintained XLSX spreadsheet, matched by "Артикул
продавца" (offer_id) rather than SKU. See that module's own docstring for
why offer_id is the primary key (SKU is missing on roughly half the rows
of a real file) and why the "Себ-ть для UNIT" column specifically is read."""
import io

import openpyxl
import pytest

from app.models.product import Product
from app.services.product_cost_price_import import import_product_cost_price_from_file
from tests.conftest import login


def _build_xlsx(rows: list[dict]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Артикул продавца", "Арт МП", "Наименование", "Благовещенск", "Москва", "Себ-ть для UNIT"])
    for r in rows:
        ws.append([
            r.get("offer_id"), r.get("sku"), r.get("name"), r.get("blago", 0),
            r.get("moscow", ""), r.get("cost", ""),
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _seed_product(db_session, store_id, offer_id, name="Товар", sku=None):
    product = Product(store_id=store_id, ozon_sku=sku or offer_id, offer_id=offer_id, name=name)
    db_session.add(product)
    db_session.commit()
    return product


def test_matches_by_offer_id_and_parses_ru_formatted_cost(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, "art-1")

    content = _build_xlsx([{"offer_id": "art-1", "cost": "1\xa0418,83"}])

    result = import_product_cost_price_from_file(db_session, store_id=store_id, filename="cost.xlsx", content=content)
    db_session.commit()

    assert result.created == 1
    assert not result.errors
    product = db_session.query(Product).filter(Product.store_id == store_id, Product.offer_id == "art-1").first()
    assert float(product.cost_price_rub) == 1418.83


def test_row_without_sku_still_matches_by_offer_id(db_session, two_stores_with_users):
    """Half of a real file's rows had no SKU/name at all — offer_id alone
    must still be enough to match."""
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, "art-no-sku")

    content = _build_xlsx([{"offer_id": "art-no-sku", "sku": None, "name": None, "cost": "591,35"}])

    result = import_product_cost_price_from_file(db_session, store_id=store_id, filename="cost.xlsx", content=content)

    assert result.created == 1
    product = db_session.query(Product).filter(Product.store_id == store_id, Product.offer_id == "art-no-sku").first()
    assert float(product.cost_price_rub) == 591.35


def test_uses_cost_column_not_region_price_column(db_session, two_stores_with_users):
    """"Себ-ть для UNIT" and "Москва" can genuinely differ — the region
    price column must never be used instead."""
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, "art-1")

    content = _build_xlsx([{"offer_id": "art-1", "moscow": "999,00", "cost": "500,00"}])

    import_product_cost_price_from_file(db_session, store_id=store_id, filename="cost.xlsx", content=content)
    product = db_session.query(Product).filter(Product.store_id == store_id, Product.offer_id == "art-1").first()
    assert float(product.cost_price_rub) == 500.0


def test_offer_id_not_in_catalog_reported_not_crashed(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id

    content = _build_xlsx([{"offer_id": "unknown-art", "cost": "100,00"}])

    result = import_product_cost_price_from_file(db_session, store_id=store_id, filename="cost.xlsx", content=content)

    assert result.created == 0
    assert any("unknown-art" in e for e in result.errors)


def test_missing_required_columns_reports_error(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Артикул продавца", "Что-то ещё"])
    ws.append(["art-1", 5])
    buf = io.BytesIO()
    wb.save(buf)

    result = import_product_cost_price_from_file(db_session, store_id=store_id, filename="cost.xlsx", content=buf.getvalue())

    assert result.created == 0
    assert any("отсутствуют обязательные колонки" in e for e in result.errors)


def test_non_xlsx_extension_rejected(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    result = import_product_cost_price_from_file(db_session, store_id=store_id, filename="cost.csv", content=b"whatever")
    assert result.created == 0
    assert any(".xlsx" in e for e in result.errors)


def test_reupload_overwrites_previous_value(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, "art-1")

    first = _build_xlsx([{"offer_id": "art-1", "cost": "100,00"}])
    import_product_cost_price_from_file(db_session, store_id=store_id, filename="cost.xlsx", content=first)
    db_session.commit()

    second = _build_xlsx([{"offer_id": "art-1", "cost": "200,00"}])
    import_product_cost_price_from_file(db_session, store_id=store_id, filename="cost.xlsx", content=second)
    db_session.commit()

    product = db_session.query(Product).filter(Product.store_id == store_id, Product.offer_id == "art-1").first()
    assert float(product.cost_price_rub) == 200.0


def test_upload_route_end_to_end(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    _seed_product(db_session, store_id, "art-1")
    content = _build_xlsx([{"offer_id": "art-1", "cost": "321,50"}])

    login(client, "owner_a@example.com", "password123")
    resp = client.post(
        f"/api/stores/{store_id}/products/upload-cost-price",
        files={"file": ("cost.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 1
    assert body["errors"] == []

    product_id = db_session.query(Product).filter(Product.store_id == store_id, Product.offer_id == "art-1").first().id
    resp2 = client.get(f"/api/stores/{store_id}/products/{product_id}")
    assert resp2.status_code == 200
    assert float(resp2.json()["cost_price_rub"]) == 321.50


def test_upload_route_requires_manager_role(client, db_session, two_stores_with_users):
    from app.core.security import hash_password
    from app.models.membership import StoreMembership, StoreRole
    from app.models.user import User

    d = two_stores_with_users
    viewer = User(email="cost_viewer@example.com", full_name="Viewer", password_hash=hash_password("password123"))
    db_session.add(viewer)
    db_session.flush()
    db_session.add(StoreMembership(user_id=viewer.id, store_id=d["store_a"].id, role=StoreRole.VIEWER))
    db_session.commit()

    content = _build_xlsx([{"offer_id": "art-1", "cost": "100,00"}])
    login(client, "cost_viewer@example.com", "password123")
    resp = client.post(
        f"/api/stores/{d['store_a'].id}/products/upload-cost-price",
        files={"file": ("cost.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 403
