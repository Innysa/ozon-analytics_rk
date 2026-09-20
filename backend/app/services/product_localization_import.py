"""Import of Ozon's own «Планирование поставок → Локальность продаж» export
(XLSX, "По товарам" view) — the ONLY confirmed source of per-product
localization share found so far. See README's «РНП Товары» section for the
full history: Ozon Seller API's `/v1/rating/summary` gives ONE number for
the whole store (no SKU dimension), and `/v1/product/rating-by-sku`
(checked 2026-09-20) turned out to be an unrelated content-completeness
score — this cabinet report is a genuinely different Ozon tool ("Планирование
поставок", FBO supply planning) with no confirmed API equivalent, so it can
only be imported the same way as the other manual exports in this project
(Аналитика → Товары, Аналитика → Запросы).

Structure verified against a real exported file (2026-09-20, one store,
1517 rows): a few metadata lines at the top (the first one, "Период: X -
Y", is parsed for the report's own period-end date), a blank line, then a
SINGLE header row (unlike product_analytics_import's two-row merged
header), then one row per (SKU, Кластер) pair — Ozon computes «Доля
локальных продаж» PER CLUSTER, not once per product, so a product
appearing in N clusters gets N rows here.

This module collapses that into ONE percentage per SKU — a weighted
average across clusters, weighted by that cluster's own «Среднесуточные
продажи, шт. за 28дн» (so a cluster with negligible sales doesn't skew the
number as much as a cluster carrying most of the product's real volume).
CONFIRMED reasonable on the real file: the weighted store-wide figure this
produces (~51%) landed close to the account's own headline "Доля локальных
продаж: 58%" shown in the cabinet for a similar (but not identical) period
— a few points apart is expected given the periods don't exactly match and
Ozon's own internal weighting isn't published, not a sign the formula is
wrong. A SKU whose every cluster row has zero weight (no measurable sales)
gets no localization_pct rather than a bogus 0%/0% division.

Deliberately does NOT store the raw per-cluster breakdown anywhere — only
the collapsed per-SKU percentage, on Product.localization_pct, mirroring
how cost_price_rub is a single per-product value uploaded/entered once and
overwritten by future imports rather than accumulated as history."""
from __future__ import annotations

import io
import re
from datetime import date, datetime

import pandas as pd

from app.models.product import Product
from app.services.import_common import ImportResult, clean_ru_formatted_number, clean_str
from app.services.xlsx_compat import tolerant_xlsx_bytes

_PERIOD_RE = re.compile(r"Период:\s*\d{2}\.\d{2}\.\d{4}\s*-\s*(\d{2}\.\d{2}\.\d{4})")


def _parse_period_end(raw: pd.DataFrame) -> date | None:
    """Scans the first few metadata rows for "Период: 23.08.2026 -
    19.09.2026" and returns the END date — shown to the user as "по
    состоянию на <date>", same convention as StoreRatingSummary.
    calculation_date."""
    for i in range(min(5, len(raw))):
        text = str(raw.iat[i, 0]) if raw.shape[1] > 0 else ""
        match = _PERIOD_RE.search(text)
        if match:
            try:
                return datetime.strptime(match.group(1), "%d.%m.%Y").date()
            except ValueError:
                return None
    return None


def _find_header_row(raw: pd.DataFrame) -> int | None:
    """Anchored on a row containing BOTH "SKU" and "Кластер" — distinguishes
    this report's single-row header from product_analytics_import's
    two-row merged one (which also has a "SKU" cell but no "Кластер")."""
    for i in range(len(raw)):
        row_values = {str(v).strip() if v is not None else "" for v in raw.iloc[i]}
        if "SKU" in row_values and "Кластер" in row_values:
            return i
    return None


def import_product_localization_from_file(
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
        raw = pd.read_excel(io.BytesIO(tolerant_xlsx_bytes(content)), header=None, dtype=object)
    except Exception as exc:
        result.errors.append(f"Не удалось прочитать файл: {exc}")
        return result

    if raw.empty:
        result.errors.append("Файл пуст")
        return result

    period_end = _parse_period_end(raw)

    header_idx = _find_header_row(raw)
    if header_idx is None:
        result.errors.append("Не найдена строка заголовков (ожидаются колонки 'SKU' и 'Кластер')")
        return result

    header = raw.iloc[header_idx]
    columns: dict[str, int] = {}
    for idx, label in enumerate(header):
        text = clean_str(label)
        if text == "SKU":
            columns["sku"] = idx
        elif text == "Доля локальных продаж":
            columns["share"] = idx
        elif text == "Среднесуточные продажи, шт. за 28дн":
            columns["weight"] = idx

    required = {"sku", "share", "weight"}
    missing = required - columns.keys()
    if missing:
        result.errors.append(
            f"В файле отсутствуют обязательные колонки: {', '.join(sorted(missing))} "
            "(ожидается экспорт «Планирование поставок → Локальность продаж», вкладка «По товарам»)"
        )
        return result

    weighted_share_sum: dict[str, float] = {}
    weight_sum: dict[str, float] = {}

    for _, row in raw.iloc[header_idx + 1 :].iterrows():
        if row.isna().all():
            continue
        sku = clean_str(row[columns["sku"]])
        if not sku:
            continue
        share = clean_ru_formatted_number(row[columns["share"]])
        weight = clean_ru_formatted_number(row[columns["weight"]])
        if share is None or weight is None or weight <= 0:
            continue
        result.fetched += 1
        weighted_share_sum[sku] = weighted_share_sum.get(sku, 0.0) + share * weight
        weight_sum[sku] = weight_sum.get(sku, 0.0) + weight

    if not weight_sum:
        result.errors.append("В файле не нашлось ни одной строки с ненулевыми продажами — нечего усреднять")
        return result

    products = {p.ozon_sku: p for p in db_session.query(Product).filter(Product.store_id == store_id).all()}
    for sku, total_weight in weight_sum.items():
        product = products.get(sku)
        if not product:
            result.errors.append(f"SKU {sku} есть в файле, но не найден в каталоге этого магазина — пропущен")
            continue
        product.localization_pct = round(weighted_share_sum[sku] / total_weight, 2)
        product.localization_period_end = period_end
        result.created += 1

    return result
