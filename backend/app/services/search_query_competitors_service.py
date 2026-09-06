"""Parses Ozon's own "Результаты по запросу" export (XLSX, sheet "Результаты
по запросу") — the full search-result page for one query, with every
competing product's position, seller, price, rating, and ad bids. Structure
confirmed against a real exported file.

Deliberately a STATELESS preview, not persisted anywhere: this is a
lower-priority, simpler companion feature ("who else shows up for this
query right now") explicitly kept separate from the position-history /
green-red highlighting logic in search_query_analytics_service.py — a
competitor snapshot has no natural (SKU, query) history key of its own (it's
one query against the WHOLE market, not one seller's product), so there is
nothing meaningful to diff it against on a later upload the way
SearchQueryStatistic rows are diffed.

Layout (confirmed against a real export):
  A metadata block at the top: "Дата: DD/MM/YYYY", "Запрос: <текст>",
  "Время: HH:MM +00", "Регион: <текст>", "Сколько позиций в выдаче: <N>".
  A single flat header row (Позиция, ID товара, Название товара, Имя
  селлера, Сводная оценка, Статус, "Ставка Оплата за клик", Стратегия,
  "Ставка Оплата за заказ", "Соответствие запросу", Отзывы, "Цена для
  покупателя", "Популярность общая", "Акции от Ozon", "Срок доставки",
  "Индекс цен"), followed by a blank row and then an explanatory-tooltip
  row (every metric column blank format cells with long descriptive text)
  before the real data rows start. Both non-data rows are skipped the same
  way as every other Ozon export handled by this app: by requiring the key
  column ("Позиция") to parse as an actual integer, not a fixed row offset.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime

import pandas as pd

from app.schemas.search_query import QueryCompetitorRowOut, QueryCompetitorsReportOut
from app.services.import_common import clean_ru_formatted_number, clean_str
from app.services.xlsx_compat import tolerant_xlsx_bytes

_DATE_RE = re.compile(r"Дата:\s*(\d{2}/\d{2}/\d{4})")
_QUERY_RE = re.compile(r"Запрос:\s*(.+)")
_REGION_RE = re.compile(r"Регион:\s*(.+)")
_TOTAL_RE = re.compile(r"Сколько позиций в выдаче:\s*(\d+)")

_COLUMN_MAP = {
    "позиция": "position",
    "id товара": "ozon_product_id",
    "название товара": "product_name",
    "имя селлера": "seller_name",
    "сводная оценка": "overall_score",
    "статус": "status",
    "ставка оплата за клик": "cpc_bid_rub",
    "стратегия": "strategy",
    "ставка оплата за заказ": "cpo_bid_text",
    "соответствие запросу": "relevance_pct",
    "отзывы": "reviews_text",
    "цена для покупателя": "price_rub",
    "популярность общая": "popularity_score",
    "акции от ozon": "ozon_promotions",
    "срок доставки": "delivery_term",
    "индекс цен": "price_index_pct",
}

_TEXT_FIELDS = {
    "product_name", "seller_name", "status", "strategy", "cpo_bid_text",
    "reviews_text", "ozon_promotions", "delivery_term",
}


def _normalize_header(h: object) -> str:
    text = str(h).replace("\n", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text.lower()).strip()


def _parse_meta_date(text: str) -> date | None:
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        return None


def _read_raw_table(filename: str, content: bytes) -> pd.DataFrame:
    if filename.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(content), header=None, dtype=object)
    xls = pd.ExcelFile(io.BytesIO(tolerant_xlsx_bytes(content)))
    sheet_name = "Результаты по запросу" if "Результаты по запросу" in xls.sheet_names else xls.sheet_names[0]
    return pd.read_excel(xls, sheet_name=sheet_name, header=None, dtype=object)


def _find_header_row(raw: pd.DataFrame) -> int | None:
    for i in range(len(raw)):
        row_values = [str(v).strip() if v is not None else "" for v in raw.iloc[i]]
        if "Позиция" in row_values:
            return i
    return None


def parse_query_competitors_report(*, filename: str, content: bytes) -> QueryCompetitorsReportOut:
    """Raises ValueError with a Russian, user-facing message on any parsing
    failure — the route turns that into a 400, since this is a synchronous
    preview, not a background sync with its own error log."""
    try:
        raw = _read_raw_table(filename, content)
    except Exception as exc:
        raise ValueError(f"Не удалось прочитать файл: {exc}") from exc

    if raw.empty:
        raise ValueError("Файл пуст")

    generated_at: date | None = None
    query_text: str | None = None
    region: str | None = None
    positions_in_results: int | None = None
    for i in range(min(len(raw), 10)):
        for value in raw.iloc[i]:
            if value is None:
                continue
            text = str(value)
            if (m := _DATE_RE.search(text)):
                generated_at = _parse_meta_date(m.group(1))
            if (m := _QUERY_RE.search(text)):
                query_text = m.group(1).strip()
            if (m := _REGION_RE.search(text)):
                region = m.group(1).strip()
            if (m := _TOTAL_RE.search(text)):
                positions_in_results = int(m.group(1))

    if not query_text:
        raise ValueError("Не найден поисковый запрос в шапке файла (ожидается строка 'Запрос: ...')")

    header_row_idx = _find_header_row(raw)
    if header_row_idx is None:
        raise ValueError("Не найдена строка заголовков (ожидается колонка 'Позиция')")

    header_row = raw.iloc[header_row_idx]
    columns: dict[str, int] = {}
    for idx in range(len(header_row)):
        canonical = _COLUMN_MAP.get(_normalize_header(header_row[idx]))
        if canonical and canonical not in columns:
            columns[canonical] = idx

    if "position" not in columns:
        raise ValueError("Не найдена колонка 'Позиция'")

    def cell(row, key):
        idx = columns.get(key)
        return row[idx] if idx is not None else None

    rows: list[QueryCompetitorRowOut] = []
    for _, row in raw.iloc[header_row_idx + 1 :].iterrows():
        if row.isna().all():
            continue
        position_raw = cell(row, "position")
        try:
            position = int(clean_ru_formatted_number(position_raw))
        except (TypeError, ValueError):
            # Blank separator row or the explanatory-tooltip row — neither
            # has a numeric "Позиция", same skip precedent as every other
            # Ozon export this app parses.
            continue

        field_values: dict[str, object] = {}
        for canonical_field, idx in columns.items():
            if canonical_field in ("position",):
                continue
            raw_value = row[idx] if idx < len(row) else None
            if canonical_field == "ozon_product_id":
                parsed = clean_ru_formatted_number(raw_value)
                field_values[canonical_field] = int(parsed) if parsed is not None else None
            elif canonical_field in _TEXT_FIELDS:
                field_values[canonical_field] = clean_str(raw_value)
            else:
                field_values[canonical_field] = clean_ru_formatted_number(raw_value)

        rows.append(QueryCompetitorRowOut(position=position, **field_values))

    return QueryCompetitorsReportOut(
        query_text=query_text,
        region=region,
        generated_at=generated_at,
        positions_in_results=positions_in_results,
        rows=rows,
    )
