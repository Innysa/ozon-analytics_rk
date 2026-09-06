"""Parses the ZIP report returned by Ozon Performance API's async
statistics-report flow (GET /api/client/statistics/report) into plain dicts
ready to build AdvertisingDailyStatistic rows from.

Structure confirmed against a real account (see the ТЗ this was built from,
and app.models.advertising_daily_statistic's module docstring):
  - The ZIP contains one CSV per requested campaign, named
    "{campaignId}_{dateFrom}-{dateTo}.csv" (dates DD.MM.YYYY). The campaign
    id is taken from the filename, not from a column — the CSV itself has no
    campaign-id column.
  - Each CSV is ';'-delimited and has: a title line (report name/period, not
    data), a header line (Russian column labels), one row per (SKU, day),
    and a final "Всего" (total) row.
  - Encoding is not guaranteed to be one or the other — Ozon has been
    observed emitting both UTF-8-with-BOM and Windows-1251 for this export —
    so decoding tries utf-8-sig first and falls back to cp1251.
  - The totals row is skipped using the same precedent as the "Итого и
    среднее" row in product_analytics_import: skip a row for which the
    "День" column doesn't parse as an actual date, rather than assuming a
    fixed row offset. A campaign with zero impressions for the whole period
    still gets a fully-zero data row plus a zero totals row — neither case
    is an error.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime

from app.services.import_common import clean_ru_formatted_int, clean_ru_formatted_number, clean_str

_FILENAME_RE = re.compile(r"^(?P<campaign_id>[^_]+)_(?P<date_from>\d{2}\.\d{2}\.\d{4})-(?P<date_to>\d{2}\.\d{2}\.\d{4})\.csv$")

# normalized header -> canonical field name (same normalization convention as
# advertising_import.py / product_analytics_import.py: lowercase, strip
# ,%₽().  punctuation, collapse whitespace)
_COLUMN_MAP = {
    "sku": "sku",
    "название товара": "product_name",
    "день": "date",
    "цена товара": "product_price_rub",
    "тип страницы": "page_type",
    "условие показа": "impression_condition",
    "показы": "impressions",
    "клики": "clicks",
    "ctr": "ctr_pct_ozon",
    "в корзину": "cart_additions",
    "средняя ставка руб": "avg_bid_rub_ozon",
    "расход с ндс": "spend_rub",
    "заказы": "orders",
    "выручка": "revenue_rub",
    "заказы модели": "orders_model",
    "выручка с заказов модели": "revenue_model_rub",
}

_INT_FIELDS = {"impressions", "clicks", "cart_additions", "orders", "orders_model"}
_TEXT_FIELDS = {"sku", "product_name", "page_type", "impression_condition"}


def _normalize_header(h: object) -> str:
    text = re.sub(r"[,%₽().]", "", str(h).lower())
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(value: object) -> date | None:
    text = str(value).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _decode_csv_bytes(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1251")


@dataclass
class ParsedStatisticsReport:
    rows: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _parse_campaign_csv(campaign_id: str, text: str, warnings: list[str]) -> list[dict]:
    lines = text.splitlines()
    if len(lines) < 2:
        warnings.append(f"Кампания {campaign_id}: файл отчёта пуст или содержит только заголовок")
        return []

    reader = csv.reader(lines, delimiter=";")
    all_rows = list(reader)
    # all_rows[0] is the title line (report name/period) — not a header, skip it.
    header_row = all_rows[1] if len(all_rows) > 1 else []
    columns: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        canonical = _COLUMN_MAP.get(_normalize_header(cell))
        if canonical and canonical not in columns:
            columns[canonical] = idx

    if "date" not in columns:
        warnings.append(f"Кампания {campaign_id}: в отчёте не найдена колонка 'День'")
        return []

    def cell(row: list[str], key: str) -> str | None:
        idx = columns.get(key)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    parsed_rows: list[dict] = []
    for row in all_rows[2:]:
        if not row or all(not v.strip() for v in row):
            continue

        parsed_date = _parse_date(cell(row, "date"))
        if parsed_date is None:
            # The "Всего" totals row (or any other non-data row) has no
            # parseable date here — skip it silently, same precedent as
            # product_analytics_import's "Итого и среднее" row.
            continue

        sku = clean_str(cell(row, "sku"))
        if not sku:
            warnings.append(f"Кампания {campaign_id}: пропущена строка без SKU за {parsed_date}")
            continue

        field_values: dict[str, object] = {"ozon_campaign_id": campaign_id, "sku": sku, "date": parsed_date}
        for canonical_field, idx in columns.items():
            if canonical_field in ("sku", "date"):
                continue
            raw_value = row[idx] if idx < len(row) else None
            if canonical_field in _TEXT_FIELDS:
                field_values[canonical_field] = clean_str(raw_value)
            elif canonical_field in _INT_FIELDS:
                field_values[canonical_field] = clean_ru_formatted_int(raw_value)
            else:
                field_values[canonical_field] = clean_ru_formatted_number(raw_value)

        field_values["raw_payload"] = json.dumps(
            {header_row[i] if i < len(header_row) else str(i): v for i, v in enumerate(row)},
            ensure_ascii=False,
        )
        parsed_rows.append(field_values)

    return parsed_rows


def parse_statistics_report_zip(zip_bytes: bytes) -> ParsedStatisticsReport:
    result = ParsedStatisticsReport()
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        result.warnings.append(f"Не удалось разобрать ZIP-архив отчёта: {exc}")
        return result

    for name in zf.namelist():
        basename = name.rsplit("/", 1)[-1]
        if not basename.lower().endswith(".csv"):
            continue
        match = _FILENAME_RE.match(basename)
        campaign_id = match.group("campaign_id") if match else basename.rsplit(".", 1)[0].split("_")[0]
        raw = zf.read(name)
        try:
            text = _decode_csv_bytes(raw)
        except UnicodeDecodeError as exc:
            result.warnings.append(f"Кампания {campaign_id}: не удалось определить кодировку файла отчёта: {exc}")
            continue
        result.rows.extend(_parse_campaign_csv(campaign_id, text, result.warnings))

    return result
