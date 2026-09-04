"""Import of Ozon's own "Аналитика → Запросы" export (XLSX) — search-query
statistics (people who searched, people who saw the product, its position
in that query's results, search→card and search→order conversion, units
and revenue ordered via that query). Structure verified against a real
exported file (single product, sheet "Запросы моего товара", 150 distinct
queries).

Layout (confirmed against a real export):
  A metadata block at the top (Дата, Время — report generation timestamp;
  Дата начала / Дата конца — the report's period, format DD/MM/YYYY).
  A single flat header row (SKU, Артикул, Название товара, Запросы товара,
  Человек искало, Человек увидело, Позиция товара, Конверсия из поиска в
  карточку, Конверсия из поиска в заказ, Заказано товаров по запросам,
  Заказано на сумму по запросам).
  Below the header, the data is a hierarchical BLOCK, not a flat table: one
  "product header" row (SKU/Артикул/Название filled, every metric column
  blank) followed immediately by all of that product's query-detail rows
  (SKU/Артикул/Название blank, query text + metrics filled), then the next
  product's header row, and so on. This importer carries the current
  product's identity forward across the detail rows.

There is no per-row date column — like the advertising-statistics export,
this is a period-level report; period_start/period_end (parsed from the
metadata block) are the unit of time each row covers. Every metric cell is
pre-formatted display text (space-grouped thousands, comma decimals, '₽'/
'%' suffixes — see app.services.import_common.clean_ru_formatted_number),
unlike the product-card/advertising exports which hand back plain numeric
cells.
"""
from __future__ import annotations

import io
import json
import re
from datetime import date, datetime

import pandas as pd

from app.models.product import Product
from app.models.search_query_statistic import SearchQueryStatistic
from app.services.import_common import ImportResult, clean_ru_formatted_int, clean_ru_formatted_number, clean_str
from app.services.xlsx_compat import tolerant_xlsx_bytes

_PERIOD_START_RE = re.compile(r"Дата начала:\s*(\d{2}/\d{2}/\d{4})")
_PERIOD_END_RE = re.compile(r"Дата конца:\s*(\d{2}/\d{2}/\d{4})")

_COLUMN_MAP = {
    "sku": "sku",
    "артикул": "offer_id",
    "название товара": "product_name",
    "запросы товара": "query_text",
    "человек искало": "people_searched",
    "человек увидело": "people_saw",
    "позиция товара": "position_ozon",
    "конверсия из поиска в карточку": "conv_search_to_card_pct_ozon",
    "конверсия из поиска в заказ": "conv_search_to_order_pct_ozon",
    "заказано товаров по запросам": "ordered_units_by_query",
    "заказано на сумму по запросам": "ordered_sum_by_query_rub",
}

_INT_FIELDS = {"people_searched", "people_saw", "ordered_units_by_query"}


def _normalize_header(h: object) -> str:
    text = str(h).replace("\n", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text.lower()).strip()


def _parse_period_date(text: str) -> date | None:
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        return None


def _read_raw_table(filename: str, content: bytes) -> pd.DataFrame:
    if filename.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(content), header=None, dtype=object)
    xls = pd.ExcelFile(io.BytesIO(tolerant_xlsx_bytes(content)))
    sheet_name = "Запросы моего товара" if "Запросы моего товара" in xls.sheet_names else xls.sheet_names[0]
    return pd.read_excel(xls, sheet_name=sheet_name, header=None, dtype=object)


def _find_period(raw: pd.DataFrame) -> tuple[date, date] | None:
    period_start: date | None = None
    period_end: date | None = None
    for i in range(min(len(raw), 10)):
        for value in raw.iloc[i]:
            if value is None:
                continue
            text = str(value)
            m = _PERIOD_START_RE.search(text)
            if m:
                period_start = _parse_period_date(m.group(1))
            m = _PERIOD_END_RE.search(text)
            if m:
                period_end = _parse_period_date(m.group(1))
    if period_start is None or period_end is None:
        return None
    return period_start, period_end


def _find_header_row(raw: pd.DataFrame) -> int | None:
    for i in range(len(raw)):
        row_values = [str(v).strip() if v is not None else "" for v in raw.iloc[i]]
        if "SKU" in row_values:
            return i
    return None


def import_search_query_statistics_from_file(
    db_session,
    *,
    store_id: str,
    filename: str,
    content: bytes,
) -> ImportResult:
    result = ImportResult()
    try:
        raw = _read_raw_table(filename, content)
    except Exception as exc:
        result.errors.append(f"Не удалось прочитать файл: {exc}")
        return result

    if raw.empty:
        result.errors.append("Файл пуст")
        return result

    period = _find_period(raw)
    if period is None:
        result.errors.append("Не найден период отчёта (ожидаются строки 'Дата начала:' / 'Дата конца:')")
        return result
    period_start, period_end = period

    header_row_idx = _find_header_row(raw)
    if header_row_idx is None:
        result.errors.append("Не найдена строка заголовков (ожидается колонка 'SKU')")
        return result

    header_row = raw.iloc[header_row_idx]
    columns: dict[str, int] = {}
    for idx in range(len(header_row)):
        canonical = _COLUMN_MAP.get(_normalize_header(header_row[idx]))
        if canonical and canonical not in columns:
            columns[canonical] = idx

    required = {"sku", "query_text"}
    missing = required - columns.keys()
    if missing:
        result.errors.append(f"В файле отсутствуют обязательные колонки: {', '.join(sorted(missing))}")
        return result

    existing_keys = {
        (s.ozon_sku, s.query_text)
        for s in db_session.query(SearchQueryStatistic.ozon_sku, SearchQueryStatistic.query_text)
        .filter(
            SearchQueryStatistic.store_id == store_id,
            SearchQueryStatistic.period_start == period_start,
            SearchQueryStatistic.period_end == period_end,
        ).all()
    }
    product_cache: dict[str, Product] = {
        p.ozon_sku: p for p in db_session.query(Product).filter(Product.store_id == store_id).all()
    }

    source = "csv_import" if filename.lower().endswith(".csv") else "xlsx_import"

    def cell(row, key):
        idx = columns.get(key)
        return row[idx] if idx is not None else None

    current_sku: str | None = None
    current_offer_id: str | None = None
    current_product_name: str | None = None

    for _, row in raw.iloc[header_row_idx + 1 :].iterrows():
        if row.isna().all():
            continue

        row_sku = clean_str(cell(row, "sku"))
        if row_sku:
            # Product header row: carry its identity forward, no metrics here.
            current_sku = row_sku
            current_offer_id = clean_str(cell(row, "offer_id"))
            current_product_name = clean_str(cell(row, "product_name"))
            continue

        query_text = clean_str(cell(row, "query_text"))
        if not query_text:
            continue
        if current_sku is None:
            result.errors.append(f"Строка запроса «{query_text}» встретилась раньше строки товара — пропущена")
            continue

        result.fetched += 1
        try:
            key = (current_sku, query_text)
            if key in existing_keys:
                result.skipped_duplicate += 1
                continue

            product = product_cache.get(current_sku)
            if not product:
                product = Product(
                    store_id=store_id,
                    ozon_sku=current_sku,
                    name=current_product_name or f"Товар SKU {current_sku}",
                    offer_id=current_offer_id,
                )
                db_session.add(product)
                db_session.flush()
                product_cache[current_sku] = product

            field_values: dict[str, object] = {}
            for canonical_field, idx in columns.items():
                if canonical_field in ("sku", "offer_id", "product_name", "query_text"):
                    continue
                raw_value = row[idx]
                if canonical_field in _INT_FIELDS:
                    field_values[canonical_field] = clean_ru_formatted_int(raw_value)
                else:
                    field_values[canonical_field] = clean_ru_formatted_number(raw_value)

            stat = SearchQueryStatistic(
                store_id=store_id,
                product_id=product.id,
                ozon_sku=current_sku,
                offer_id=current_offer_id,
                query_text=query_text,
                period_start=period_start,
                period_end=period_end,
                source=source,
                raw_payload=json.dumps(
                    {str(k): (None if pd.isna(v) else str(v)) for k, v in row.items()}, ensure_ascii=False
                ),
                **field_values,
            )
            db_session.add(stat)
            existing_keys.add(key)
            result.created += 1
        except Exception as exc:
            result.errors.append(f"Ошибка в строке запроса «{query_text}»: {exc}")

    return result
