"""Bulk import of себестоимость (cost price) from the seller's own XLSX
export — Ozon never exposes cost price via any API (it's the seller's
private purchase/production cost), so this is the bulk counterpart to the
single-product PUT /products/{id}/cost-price endpoint, for sellers who
keep their costs in a spreadsheet instead of typing them in one by one.

Matched by "Артикул продавца" (offer_id) rather than SKU/"Арт МП": on the
first real file checked (2026-09-24, one store, 1083 rows) EVERY row had
an offer_id, but roughly half had no SKU at all (blank "Арт МП" and
"Наименование" — likely archived/delisted listings that never got a
fresh SKU column filled in by whatever tool produced the export), so
offer_id is the only key that reliably covers the whole file.

Column to read is "Себ-ть для UNIT" specifically (per the user's explicit
instruction), not the per-region price columns ("Благовещенск"/"Москва")
that sit next to it — on that same real file the "Москва" and "Себ-ть для
UNIT" columns are equal on ~90% of rows but differ on 112 of 1083, so
they are genuinely different figures and not just a duplicated column."""
from __future__ import annotations

import io

import pandas as pd

from app.models.product import Product
from app.services.import_common import ImportResult, clean_ru_formatted_number, clean_str
from app.services.xlsx_compat import tolerant_xlsx_bytes

_OFFER_ID_COL = "Артикул продавца"
_COST_COL = "Себ-ть для UNIT"


def import_product_cost_price_from_file(
    db_session,
    *,
    store_id: str,
    filename: str,
    content: bytes,
) -> ImportResult:
    result = ImportResult()
    if not filename.lower().endswith(".xlsx"):
        result.errors.append("Поддерживается только файл .xlsx")
        return result

    try:
        df = pd.read_excel(io.BytesIO(tolerant_xlsx_bytes(content)), dtype=object)
    except Exception as exc:
        result.errors.append(f"Не удалось прочитать файл: {exc}")
        return result

    if df.empty:
        result.errors.append("Файл пуст")
        return result

    columns = {str(c).strip(): c for c in df.columns}
    missing = {_OFFER_ID_COL, _COST_COL} - columns.keys()
    if missing:
        result.errors.append(
            f"В файле отсутствуют обязательные колонки: {', '.join(sorted(missing))}"
        )
        return result

    products = {p.offer_id: p for p in db_session.query(Product).filter(Product.store_id == store_id).all() if p.offer_id}

    unmatched: list[str] = []
    for _, row in df.iterrows():
        offer_id = clean_str(row[columns[_OFFER_ID_COL]])
        if not offer_id:
            continue
        result.fetched += 1
        cost = clean_ru_formatted_number(row[columns[_COST_COL]])
        if cost is None:
            continue
        product = products.get(offer_id)
        if not product:
            unmatched.append(offer_id)
            continue
        product.cost_price_rub = round(cost, 2)
        result.created += 1

    if unmatched:
        shown = ", ".join(unmatched[:20])
        more = f" и ещё {len(unmatched) - 20}" if len(unmatched) > 20 else ""
        result.errors.append(
            f"Не найдено в каталоге этого магазина ({len(unmatched)} артикулов): {shown}{more}"
        )

    return result
