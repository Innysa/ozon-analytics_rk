"""Import of Ozon's own "Аналитика → Товары" export (CSV/XLSX) — product-card
daily statistics (sales funnel, conversions, orders/delivery/buyout/
cancellations/returns, price, stock, reviews, rating). Structure verified
against a real exported file (single product, 7 days).

Layout (confirmed against a real export, sheet "По товарам"):
  A block of metadata lines at the top (Период, Товар, Категория, Цена,
  Схема работы, Признак товара, Платформа, Продавец — not parsed, each data
  row carries its own values for the columns that matter).
  A two-row header: row N has the plain-column labels (Товары, Категория N
  уровня, Бренд, Модель, Схема работы, SKU, Артикул, День) and the group
  labels for the metric block ("Продажи", "Воронка продаж", "Факторы
  продаж"); row N+1 has the actual sub-column labels for the metric block
  (Ozon uses a merged two-row header — a cell with no row-(N+1) label falls
  back to its row-N value). Header labels bake the report's date range into
  themselves (e.g. "ABC-анализ по сумме заказов 28.08.2026 – 03.09.2026");
  that suffix is stripped before matching so this works on any period.
  Below the header: an explanatory-text row, then an aggregate "Итого и
  среднее" row, then one row per (product, day). Both non-data rows are
  skipped by requiring the "День" column to parse as an actual date — a more
  robust anchor than a fixed row offset, since Ozon's descriptive text can
  change.

Unlike the advertising statistics export, this one genuinely has one row per
day (column "День"), so no period-aggregation compromise is needed — see
app.models.product_card_statistic.
"""
from __future__ import annotations

import io
import json
import re
from datetime import date, datetime

import pandas as pd

from app.models.product import Product
from app.models.product_card_statistic import ProductCardStatistic
from app.services.import_common import ImportResult, clean_int, clean_number, clean_str
from app.services.xlsx_compat import tolerant_xlsx_bytes

_DATE_RANGE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}\s*[–-]\s*\d{2}\.\d{2}\.\d{4}")

# normalized header -> canonical field name. Only columns we store as typed
# fields are listed; anything else (e.g. "Модель", "Доля в общей сумме
# заказов") is still preserved in raw_payload, just not modeled separately.
_COLUMN_MAP = {
    "товары": "product_name",
    "категория 1 уровня": "category_l1",
    "категория 2 уровня": "category_l2",
    "категория 3 уровня": "category_l3",
    "бренд": "brand",
    "схема работы": "fulfillment_scheme",
    "sku": "sku",
    "артикул": "offer_id",
    "день": "date",
    "abc-анализ по сумме заказов": "abc_by_revenue_ozon",
    "abc-анализ по количеству заказов": "abc_by_units_ozon",
    "заказано на сумму по цене реализации": "ordered_sum_actual_price_rub",
    "заказано на сумму по предельной цене": "ordered_sum_list_price_rub",
    "позиция в поиске и каталоге": "search_catalog_position_ozon",
    "показы всего": "impressions_total",
    "конверсия из показа в заказ": "conv_impression_to_order_pct_ozon",
    "показы в поиске и каталоге": "impressions_search_catalog",
    "конверсия из поиска и каталога в корзину": "conv_search_catalog_to_cart_pct_ozon",
    "добавления из поиска и каталога в корзину": "cart_adds_from_search_catalog",
    "конверсия из поиска и каталога в карточку": "conv_search_catalog_to_card_pct_ozon",
    "посещения карточки товара": "card_visits",
    "конверсия из карточки в корзину": "conv_card_to_cart_pct_ozon",
    "добавления из карточки в корзину": "cart_adds_from_card",
    "конверсия в корзину общая": "conv_to_cart_total_pct_ozon",
    "добавления в корзину всего": "cart_adds_total",
    "конверсия из корзины в заказ": "conv_cart_to_order_pct_ozon",
    "заказано товаров": "ordered_units",
    "доставлено товаров": "delivered_units",
    "конверсия из заказа в выкуп": "conv_order_to_buyout_pct_ozon",
    "выкуплено товаров": "bought_out_units",
    "отменено товаров на дату отмены": "cancelled_units_by_cancel_date",
    "отменено товаров на дату заказа": "cancelled_units_by_order_date",
    "возвращено товаров на дату возврата": "returned_units_by_return_date",
    "возвращено товаров на дату заказа": "returned_units_by_order_date",
    "средняя цена": "avg_price_rub",
    "скидка от медианной цены": "discount_from_median_pct_ozon",
    "индекс цен": "price_index_label_ozon",
    "дней в акциях": "promo_days_label_ozon",
    "дрр": "drr_pct_ozon",
    "дней с продвижением оплата за клик": "paid_promo_days_label_ozon",
    "остаток на конец периода": "stock_end_of_period",
    "отзывы": "reviews_count",
    "рейтинг товара": "rating",
}

# fields that must stay text (not coerced to numbers) — categorical or "X из Y" style
_TEXT_FIELDS = {"abc_by_revenue_ozon", "abc_by_units_ozon", "price_index_label_ozon", "promo_days_label_ozon", "paid_promo_days_label_ozon"}
_INT_FIELDS = {
    "impressions_total", "impressions_search_catalog", "card_visits",
    "cart_adds_from_search_catalog", "cart_adds_from_card", "cart_adds_total",
    "ordered_units", "delivered_units", "bought_out_units",
    "cancelled_units_by_cancel_date", "cancelled_units_by_order_date",
    "returned_units_by_return_date", "returned_units_by_order_date",
    "stock_end_of_period", "reviews_count",
}


def _normalize_header(h: object) -> str:
    text = str(h).replace("\n", " ")
    text = _DATE_RANGE_RE.sub("", text)
    text = re.sub(r"[,()]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _read_raw_table(filename: str, content: bytes) -> pd.DataFrame:
    if filename.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(content), header=None, dtype=object)
    xls = pd.ExcelFile(io.BytesIO(tolerant_xlsx_bytes(content)))
    sheet_name = "По товарам" if "По товарам" in xls.sheet_names else xls.sheet_names[0]
    return pd.read_excel(xls, sheet_name=sheet_name, header=None, dtype=object)


def _find_header_rows(raw: pd.DataFrame) -> tuple[int, int] | None:
    """Locate the two-row merged header by finding the row whose values
    include a "SKU" cell (present only in the group-label header row)."""
    for i in range(len(raw)):
        row_values = [str(v).strip() if v is not None else "" for v in raw.iloc[i]]
        if "SKU" in row_values:
            return i, i + 1
    return None


def import_product_card_statistics_from_file(
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

    header_rows = _find_header_rows(raw)
    if not header_rows:
        result.errors.append("Не найдена строка заголовков (ожидается колонка 'SKU')")
        return result
    group_row_idx, sub_row_idx = header_rows

    group_row = raw.iloc[group_row_idx]
    sub_row = raw.iloc[sub_row_idx] if sub_row_idx < len(raw) else [None] * len(group_row)

    columns: dict[str, int] = {}
    for idx in range(len(group_row)):
        sub_value = sub_row[idx]
        raw_label = group_row[idx] if pd.isna(sub_value) else sub_value
        canonical = _COLUMN_MAP.get(_normalize_header(raw_label))
        if canonical and canonical not in columns:
            columns[canonical] = idx

    required = {"sku", "date"}
    missing = required - columns.keys()
    if missing:
        result.errors.append(f"В файле отсутствуют обязательные колонки: {', '.join(sorted(missing))}")
        return result

    existing_keys = {
        (s.ozon_sku, s.date)
        for s in db_session.query(ProductCardStatistic.ozon_sku, ProductCardStatistic.date)
        .filter(ProductCardStatistic.store_id == store_id).all()
    }
    product_cache: dict[str, Product] = {
        p.ozon_sku: p for p in db_session.query(Product).filter(Product.store_id == store_id).all()
    }

    source = "csv_import" if filename.lower().endswith(".csv") else "xlsx_import"

    def cell(row, key):
        idx = columns.get(key)
        return row[idx] if idx is not None else None

    data_start = sub_row_idx + 1
    for _, row in raw.iloc[data_start:].iterrows():
        if row.isna().all():
            continue

        parsed_date = _parse_date(cell(row, "date"))
        if parsed_date is None:
            # Not a data row — the explanatory-text row or the "Итого и среднее"
            # aggregate row both have no parseable date here; skip silently.
            continue

        result.fetched += 1
        try:
            sku = clean_str(cell(row, "sku"))
            if not sku:
                result.errors.append(f"Пропущена строка без SKU за {parsed_date}")
                continue

            key = (sku, parsed_date)
            if key in existing_keys:
                result.skipped_duplicate += 1
                continue

            product = product_cache.get(sku)
            if not product:
                product_name = clean_str(cell(row, "product_name")) or f"Товар SKU {sku}"
                product = Product(
                    store_id=store_id, ozon_sku=sku, name=product_name,
                    offer_id=clean_str(cell(row, "offer_id")),
                )
                db_session.add(product)
                db_session.flush()
                product_cache[sku] = product

            _explicit_fields = {
                "sku", "date", "product_name", "offer_id",
                "category_l1", "category_l2", "category_l3", "brand", "fulfillment_scheme",
            }
            field_values: dict[str, object] = {}
            for canonical_field, idx in columns.items():
                if canonical_field in _explicit_fields:
                    continue
                raw_value = row[idx]
                if canonical_field in _TEXT_FIELDS:
                    field_values[canonical_field] = clean_str(raw_value)
                elif canonical_field in _INT_FIELDS:
                    field_values[canonical_field] = clean_int(raw_value)
                else:
                    field_values[canonical_field] = clean_number(raw_value)

            stat = ProductCardStatistic(
                store_id=store_id,
                product_id=product.id,
                ozon_sku=sku,
                offer_id=clean_str(cell(row, "offer_id")),
                category_l1=clean_str(cell(row, "category_l1")),
                category_l2=clean_str(cell(row, "category_l2")),
                category_l3=clean_str(cell(row, "category_l3")),
                brand=clean_str(cell(row, "brand")),
                fulfillment_scheme=clean_str(cell(row, "fulfillment_scheme")),
                date=parsed_date,
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
            result.errors.append(f"Ошибка в строке за {parsed_date}: {exc}")

    return result
