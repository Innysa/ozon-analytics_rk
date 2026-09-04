"""Import of Ozon's own "Продвижение → Статистика" export (CSV/XLSX) — the
real, verified source of advertising performance data used by this app (see
app.models.advertising_statistic for why: the Performance API's async
statistics-report contract could not be confirmed field-for-field, while this
export's structure was verified against an actual file).

Expected layout (confirmed against a real export):
  Row 1, column A: "Период: DD.MM.YYYY - DD.MM.YYYY"
  Row 2: column headers (Russian, as Ozon labels them)
  Row 3+: one row per (SKU, campaign) for that period

Column headers recognized (after stripping ,%₽() and collapsing whitespace):
  SKU, Название товара, Инструмент, Место размещения, ID кампании,
  Расход ₽, ДРР в продвижении %, Продажи в продвижении ₽, Продано товаров шт,
  Продажи в продвижении с заказов модели ₽, Продано товаров модели шт,
  CTR %, Показы, Клики, Добавления в корзину шт, Конверсия в корзину %,
  ДРР (общий) %, Затраты на заказ ₽, Средняя стоимость клика ₽

The workbook's "Union" sheet (cross-listing / merged-card sales attribution)
is deliberately NOT imported here: summing its "Продажи в продвижении"
alongside the main Statistics sheet would double-count revenue already
attributed there. Supporting it would need its own, separately-reasoned
aggregation logic.

There is no per-row date — only a period. A seller who wants daily numbers
uploads one export per day (period_start == period_end); this importer never
invents a date that isn't in the source file.
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from app.models.advertising_campaign import AdvertisingCampaign
from app.models.advertising_statistic import AdvertisingStatistic
from app.models.product import Product

_PERIOD_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")

# normalized header -> canonical field name
_COLUMN_MAP = {
    "sku": "sku",
    "название товара": "product_name",
    "инструмент": "ad_tool",
    "место размещения": "placement",
    "id кампании": "campaign_id",
    "расход": "spend_rub",
    "дрр в продвижении": "drr_promo_pct_ozon",
    "продажи в продвижении": "sales_promo_rub",
    "продано товаров шт": "units_sold",
    "продажи в продвижении с заказов модели": "sales_promo_model_rub",
    "продано товаров модели шт": "units_sold_model",
    "ctr": "ctr_pct_ozon",
    "показы": "impressions",
    "клики": "clicks",
    "добавления в корзину шт": "cart_additions",
    "конверсия в корзину": "cart_conversion_pct_ozon",
    "дрр общий": "drr_total_pct_ozon",
    "затраты на заказ": "cost_per_order_rub_ozon",
    "средняя стоимость клика": "avg_cpc_rub_ozon",
}

_MISSING_SENTINELS = {"", "-", "—", "n/a", "na"}


def _normalize_header(h: object) -> str:
    text = re.sub(r"[,%₽()]", "", str(h).lower())
    return re.sub(r"\s+", " ", text).strip()


def _parse_period(cell: object) -> tuple[date, date] | None:
    match = _PERIOD_RE.search(str(cell))
    if not match:
        return None
    start, end = match.groups()
    return datetime.strptime(start, "%d.%m.%Y").date(), datetime.strptime(end, "%d.%m.%Y").date()


def _clean_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and pd.isna(value):
            return None
        return float(value)
    text = str(value).strip()
    if text.lower() in _MISSING_SENTINELS:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _clean_int(value: object) -> int | None:
    n = _clean_number(value)
    return int(n) if n is not None else None


def _clean_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in _MISSING_SENTINELS:
        return None
    return text


@dataclass
class ImportResult:
    fetched: int = 0
    created: int = 0
    skipped_duplicate: int = 0
    errors: list[str] = field(default_factory=list)


def _read_raw_table(filename: str, content: bytes) -> tuple[pd.DataFrame, str | None]:
    """Returns (raw dataframe with no header applied, warning-or-None about
    a sheet that was present but skipped)."""
    if filename.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(content), header=None, dtype=object), None

    xls = pd.ExcelFile(io.BytesIO(content))
    sheet_name = "Statistics" if "Statistics" in xls.sheet_names else xls.sheet_names[0]
    warning = None
    if "Union" in xls.sheet_names and sheet_name != "Union":
        warning = (
            "Лист 'Union' (продажи по объединённым карточкам) не импортирован — "
            "суммирование его данных с основным листом задвоило бы выручку."
        )
    raw = pd.read_excel(xls, sheet_name=sheet_name, header=None, dtype=object)
    return raw, warning


def import_advertising_statistics_from_file(
    db_session,
    *,
    store_id: str,
    filename: str,
    content: bytes,
) -> ImportResult:
    result = ImportResult()
    try:
        raw, warning = _read_raw_table(filename, content)
    except Exception as exc:
        result.errors.append(f"Не удалось прочитать файл: {exc}")
        return result
    if warning:
        result.errors.append(warning)

    if raw.empty:
        result.errors.append("Файл пуст")
        return result

    period = _parse_period(raw.iat[0, 0])
    if not period:
        result.errors.append(
            "Не найден период отчёта в первой строке (ожидается 'Период: ДД.ММ.ГГГГ - ДД.ММ.ГГГГ')"
        )
        return result
    period_start, period_end = period

    if len(raw) < 2:
        result.errors.append("В файле нет строки заголовков")
        return result

    header_row = raw.iloc[1]
    columns: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        canonical = _COLUMN_MAP.get(_normalize_header(cell))
        if canonical and canonical not in columns:
            columns[canonical] = idx

    required = {"sku", "campaign_id", "spend_rub"}
    missing = required - columns.keys()
    if missing:
        result.errors.append(f"В файле отсутствуют обязательные колонки: {', '.join(sorted(missing))}")
        return result

    existing_keys = {
        (s.ozon_sku, s.ozon_campaign_id, s.period_start, s.period_end)
        for s in db_session.query(
            AdvertisingStatistic.ozon_sku, AdvertisingStatistic.ozon_campaign_id,
            AdvertisingStatistic.period_start, AdvertisingStatistic.period_end,
        ).filter(AdvertisingStatistic.store_id == store_id).all()
    }

    campaign_cache: dict[str, AdvertisingCampaign] = {
        c.ozon_campaign_id: c
        for c in db_session.query(AdvertisingCampaign).filter(AdvertisingCampaign.store_id == store_id).all()
    }
    product_cache: dict[str, Product] = {
        p.ozon_sku: p
        for p in db_session.query(Product).filter(Product.store_id == store_id).all()
    }

    source = "csv_import" if filename.lower().endswith(".csv") else "xlsx_import"

    def cell(row, key):
        idx = columns.get(key)
        return row[idx] if idx is not None else None

    for _, row in raw.iloc[2:].iterrows():
        if row.isna().all():
            continue
        result.fetched += 1
        try:
            sku = _clean_str(cell(row, "sku"))
            campaign_ozon_id = _clean_str(cell(row, "campaign_id"))
            if not sku or not campaign_ozon_id:
                result.errors.append("Пропущена строка без SKU или ID кампании")
                continue

            key = (sku, campaign_ozon_id, period_start, period_end)
            if key in existing_keys:
                result.skipped_duplicate += 1
                continue

            spend = _clean_number(cell(row, "spend_rub"))
            if spend is None:
                result.errors.append(f"Некорректный расход у SKU {sku}, кампания {campaign_ozon_id}")
                continue

            product = product_cache.get(sku)
            if not product:
                product_name = _clean_str(cell(row, "product_name")) or f"Товар SKU {sku}"
                product = Product(store_id=store_id, ozon_sku=sku, name=product_name)
                db_session.add(product)
                db_session.flush()
                product_cache[sku] = product

            campaign = campaign_cache.get(campaign_ozon_id)
            if not campaign:
                campaign = AdvertisingCampaign(
                    store_id=store_id, ozon_campaign_id=campaign_ozon_id, name=f"Кампания {campaign_ozon_id}"
                )
                db_session.add(campaign)
                db_session.flush()
                campaign_cache[campaign_ozon_id] = campaign

            stat = AdvertisingStatistic(
                store_id=store_id,
                product_id=product.id,
                campaign_id=campaign.id,
                ozon_sku=sku,
                ozon_campaign_id=campaign_ozon_id,
                ad_tool=_clean_str(cell(row, "ad_tool")),
                placement=_clean_str(cell(row, "placement")),
                period_start=period_start,
                period_end=period_end,
                spend_rub=spend,
                sales_promo_rub=_clean_number(cell(row, "sales_promo_rub")),
                units_sold=_clean_int(cell(row, "units_sold")),
                sales_promo_model_rub=_clean_number(cell(row, "sales_promo_model_rub")),
                units_sold_model=_clean_int(cell(row, "units_sold_model")),
                impressions=_clean_int(cell(row, "impressions")),
                clicks=_clean_int(cell(row, "clicks")),
                ctr_pct_ozon=_clean_number(cell(row, "ctr_pct_ozon")),
                cart_additions=_clean_int(cell(row, "cart_additions")),
                cart_conversion_pct_ozon=_clean_number(cell(row, "cart_conversion_pct_ozon")),
                drr_promo_pct_ozon=_clean_number(cell(row, "drr_promo_pct_ozon")),
                drr_total_pct_ozon=_clean_number(cell(row, "drr_total_pct_ozon")),
                cost_per_order_rub_ozon=_clean_number(cell(row, "cost_per_order_rub_ozon")),
                avg_cpc_rub_ozon=_clean_number(cell(row, "avg_cpc_rub_ozon")),
                source=source,
                raw_payload=json.dumps({str(k): (None if pd.isna(v) else str(v)) for k, v in row.items()}, ensure_ascii=False),
            )
            db_session.add(stat)
            existing_keys.add(key)
            result.created += 1
        except Exception as exc:
            result.errors.append(f"Ошибка в строке: {exc}")

    return result
