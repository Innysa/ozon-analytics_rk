"""Manual review import from CSV or XLSX — the fallback path required by the
spec for stores whose Ozon plan doesn't expose the reviews API.

Expected columns (case-insensitive, Russian or English header names accepted):
  ozon_review_id / id_отзыва   - required, used for dedup with store_id
  sku / артикул                - required, maps/creates a Product
  product_name / товар         - optional, used if product must be created
  rating / оценка               - required, 1-5
  text / текст                  - optional
  pros / достоинства            - optional
  cons / недостатки             - optional
  published_at / дата           - optional, ISO date/datetime
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.models.product import Product
from app.models.review import Review, ReviewSource, ReviewStatus

_COLUMN_ALIASES = {
    "ozon_review_id": {"ozon_review_id", "id_отзыва", "review_id", "id отзыва"},
    "sku": {"sku", "артикул", "ozon_sku"},
    "offer_id": {"offer_id", "артикул продавца", "seller_article"},
    "product_name": {"product_name", "товар", "название товара"},
    "rating": {"rating", "оценка"},
    "text": {"text", "текст", "текст отзыва"},
    "pros": {"pros", "достоинства"},
    "cons": {"cons", "недостатки"},
    "published_at": {"published_at", "дата", "дата отзыва"},
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
        return pd.read_csv(io.BytesIO(content))
    return pd.read_excel(io.BytesIO(content))


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
    if "ozon_review_id" not in columns or "rating" not in columns:
        result.errors.append(
            "В файле должны быть колонки для ID отзыва (ozon_review_id/ID отзыва) и оценки (rating/Оценка)"
        )
        return result

    existing_ids = {
        r.ozon_review_id
        for r in db_session.query(Review.ozon_review_id).filter(Review.store_id == store_id).all()
    }

    for _, row in df.iterrows():
        result.fetched += 1
        try:
            ozon_review_id = str(row[columns["ozon_review_id"]]).strip()
            if not ozon_review_id or ozon_review_id == "nan":
                result.errors.append("Пропущена строка без ID отзыва")
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
