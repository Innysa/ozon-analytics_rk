"""Manual review import from CSV or XLSX — the fallback path required by the
spec for stores whose Ozon plan doesn't expose the reviews API.

Expected columns (case-insensitive, Russian or English header names accepted):
  ozon_review_id / id_отзыва   - required unless order_number is present
                                  (see below), used for dedup with store_id
  order_number / номер заказа  - fallback identifier when the file has no
                                  dedicated review-id column — see below
  sku                          - Ozon's own numeric SKU; maps/creates a Product
  артикул / offer_id           - the seller's own article/offer id (kept
                                  separate from sku — see below)
  product_name / товар / название товара - optional, used if product must be created
  rating / оценка               - required, 1-5
  text / текст / текст отзыва   - optional
  pros / достоинства            - optional
  cons / недостатки             - optional
  published_at / дата / дата публикации - optional, ISO date/datetime

Real Ozon "Отзывы → Скачать отчёт" export (confirmed against an actual
downloaded file) has NO dedicated review-id column at all — its columns are
Артикул;SKU;Название товара;Номер заказа;Статус получения;Текст отзыва;Дата
публикации;Статус отзыва;Оценка;Количество фото;Количество видео;Количество
ответов на отзыв. Two things about this real file broke the original version
of this importer, which had only ever been exercised against a hand-built
simple template:

  1. It is ';'-delimited with a UTF-8 BOM, not the ',' pandas assumes by
     default — `pd.read_csv(..., sep=",")` (the implicit default) raises a
     ParserError on the first row containing a comma inside a text field
     (e.g. a product name like "Складной пуф, размер 30x30, серый"), so
     every real upload failed at the read step with zero visible effect
     beyond a terse error most sellers wouldn't parse as "wrong delimiter."
  2. It has BOTH "Артикул" (the seller's own offer id, e.g. "пуф/S/сер1/2")
     and "SKU" (Ozon's numeric sku) as separate columns — the original alias
     set treated "артикул" as just another name for the same "sku" field
     that plain "sku" mapped to, via an unordered `set`, so which column won
     for a real file depended on unspecified set-iteration behavior, and
     "Артикул" (not a valid ozon_sku) could silently become what products
     get matched/created by.
  3. It has no ID-отзыва-shaped column at all, so the original hard
     requirement for one meant every row was rejected outright regardless of
     (1)/(2). Ozon reviews are 1:1 with a purchased order line, and
     "Номер заказа" was confirmed unique per row in a real 473-row export,
     so it's used as a stable substitute identifier (prefixed "order:" so it
     can never collide with a real ozon_review_id from the API sync, which
     are UUIDs) when no dedicated review-id column is present.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.models.product import Product
from app.models.review import Review, ReviewSource, ReviewStatus
from app.services.xlsx_compat import tolerant_xlsx_bytes

# Alias lists (not sets) — order matters where a real Ozon export has
# multiple candidate columns for the same purpose (see module docstring
# point 2): the first matching alias wins, so the more specific/confirmed
# real column name is listed before a looser one kept only for backward
# compatibility with older hand-built templates.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "ozon_review_id": ("ozon_review_id", "id_отзыва", "review_id", "id отзыва"),
    "order_number": ("order_number", "номер заказа", "order number"),
    "sku": ("sku", "ozon_sku", "артикул"),
    "offer_id": ("offer_id", "артикул", "артикул продавца", "seller_article"),
    "product_name": ("product_name", "товар", "название товара"),
    "rating": ("rating", "оценка"),
    "text": ("text", "текст", "текст отзыва"),
    "pros": ("pros", "достоинства"),
    "cons": ("cons", "недостатки"),
    "published_at": ("published_at", "дата", "дата отзыва", "дата публикации"),
}


@dataclass
class ImportResult:
    fetched: int = 0
    created: int = 0
    skipped_duplicate: int = 0
    errors: list[str] = field(default_factory=list)


def _normalize_columns(df: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    lower_cols = {str(c).strip().lower(): c for c in df.columns}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_cols:
                mapping[canonical] = lower_cols[alias]
                break
    return mapping


def _read_dataframe(filename: str, content: bytes) -> pd.DataFrame:
    if filename.lower().endswith(".csv"):
        # Ozon's real export is ';'-delimited (confirmed — see module
        # docstring); try that first and only fall back to ',' if it
        # produced no more than one column, i.e. there was no ';' in the
        # file at all (a hand-built plain-comma CSV).
        df = pd.read_csv(io.BytesIO(content), sep=";")
        if df.shape[1] < 2:
            df = pd.read_csv(io.BytesIO(content), sep=",")
        return df
    return pd.read_excel(io.BytesIO(tolerant_xlsx_bytes(content)))


def import_reviews_from_file(
    db_session,
    *,
    store_id: str,
    filename: str,
    content: bytes,
) -> ImportResult:
    result = ImportResult()
    try:
        df = _read_dataframe(filename, content)
    except Exception as exc:
        result.errors.append(f"Не удалось прочитать файл: {exc}")
        return result

    columns = _normalize_columns(df)
    has_id_column = "ozon_review_id" in columns or "order_number" in columns
    if not has_id_column or "rating" not in columns:
        result.errors.append(
            "В файле должны быть колонки для ID отзыва (ozon_review_id/ID отзыва) "
            "или номера заказа (order_number/Номер заказа), и оценки (rating/Оценка)"
        )
        return result

    existing_ids = {
        r.ozon_review_id
        for r in db_session.query(Review.ozon_review_id).filter(Review.store_id == store_id).all()
    }

    for _, row in df.iterrows():
        result.fetched += 1
        try:
            if "ozon_review_id" in columns:
                ozon_review_id = str(row[columns["ozon_review_id"]]).strip()
            else:
                # No dedicated review-id column in this export (real Ozon
                # "Отзывы" report) — the order number is confirmed unique
                # per row, "order:"-prefixed so it can't collide with a real
                # ozon_review_id (a UUID) from the API sync.
                order_number = str(row[columns["order_number"]]).strip()
                ozon_review_id = f"order:{order_number}" if order_number and order_number != "nan" else ""

            if not ozon_review_id or ozon_review_id in ("nan", "order:nan"):
                result.errors.append("Пропущена строка без ID отзыва/номера заказа")
                continue
            if ozon_review_id in existing_ids:
                result.skipped_duplicate += 1
                continue

            rating_raw = row[columns["rating"]]
            rating = int(float(rating_raw))
            if not (1 <= rating <= 5):
                result.errors.append(f"Некорректная оценка у отзыва {ozon_review_id}: {rating_raw}")
                continue

            product_id = None
            if "sku" in columns:
                sku = str(row[columns["sku"]]).strip()
                if sku and sku != "nan":
                    product = (
                        db_session.query(Product)
                        .filter(Product.store_id == store_id, Product.ozon_sku == sku)
                        .first()
                    )
                    if not product:
                        name = "Без названия"
                        if "product_name" in columns:
                            candidate = str(row[columns["product_name"]]).strip()
                            if candidate and candidate != "nan":
                                name = candidate
                        offer_id = None
                        if "offer_id" in columns:
                            candidate = str(row[columns["offer_id"]]).strip()
                            offer_id = candidate if candidate and candidate != "nan" else None
                        product = Product(store_id=store_id, ozon_sku=sku, name=name, offer_id=offer_id)
                        db_session.add(product)
                        db_session.flush()
                    product_id = product.id

            published_at = None
            if "published_at" in columns:
                raw_date = row[columns["published_at"]]
                if pd.notna(raw_date):
                    try:
                        published_at = pd.to_datetime(raw_date).to_pydatetime()
                    except Exception:
                        published_at = None

            def _cell(key: str) -> str | None:
                if key not in columns:
                    return None
                value = row[columns[key]]
                if pd.isna(value):
                    return None
                text = str(value).strip()
                return text or None

            source = ReviewSource.CSV_IMPORT if filename.lower().endswith(".csv") else ReviewSource.XLSX_IMPORT
            review = Review(
                store_id=store_id,
                product_id=product_id,
                ozon_review_id=ozon_review_id,
                source=source,
                rating=rating,
                text=_cell("text"),
                pros=_cell("pros"),
                cons=_cell("cons"),
                published_at=published_at,
                status=ReviewStatus.NEW,
            )
            db_session.add(review)
            existing_ids.add(ozon_review_id)
            result.created += 1
        except Exception as exc:
            result.errors.append(f"Ошибка в строке: {exc}")

    return result
