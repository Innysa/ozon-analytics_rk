"""Import of Ozon's own downloadable «Начисления» report (Кабинет → Финансы
→ Начисления → «Скачать отчёт», XLSX) — see
app.models.accrual_report_daily_statistic's own docstring for why this is a
genuinely more precise, differently-structured source than
CashFlowStatementPeriod (real per-day dates instead of Ozon's own ~weekly
payout buckets, and Ozon's own human-readable «Группа услуг»/«Тип
начисления» instead of guessing at internal item-name substrings), and why
there's no confirmed API equivalent — same manual-re-upload situation as
product_localization_import.py.

Structure verified against a real exported file (2026-09-22, one store, 21
days, ~40 700 rows): first row "Период: 01.09.2026-21.09.2026" (informational
only — NOT used to decide which days are covered; each row's own «Дата
начисления» is), then a single header row, then one row per accrual
operation (roughly one per SKU per order per charge type).

Aggregates at import time down to one row per (accrual_date, service_group,
charge_type) — the dashboard only ever needs a per-day/per-category sum,
never the per-SKU/per-order detail this file actually carries (see the
model's own docstring for why this keeps the table small). A cell with an
unparseable date or amount is skipped and counted as an error rather than
silently dropped into an "unknown day" bucket."""
from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import date, datetime

import pandas as pd

from app.models.accrual_report_daily_statistic import AccrualReportDailyStatistic
from app.services.import_common import ImportResult, clean_number, clean_str
from app.services.xlsx_compat import tolerant_xlsx_bytes

_PERIOD_RE = re.compile(r"Период:\s*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")

_REQUIRED_COLUMNS = {
    "date": "Дата начисления",
    "group": "Группа услуг",
    "type": "Тип начисления",
    "amount": "Сумма итого, руб.",
}


def _find_header_row(raw: pd.DataFrame) -> int | None:
    """Anchored on a row containing BOTH "Группа услуг" and "Тип
    начисления" — the two columns unique to this report among this
    project's other XLSX imports."""
    for i in range(min(10, len(raw))):
        row_values = {clean_str(v) for v in raw.iloc[i]}
        if "Группа услуг" in row_values and "Тип начисления" in row_values:
            return i
    return None


def _parse_cell_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_str(value)
    if not text:
        return None
    text = text.split(" ")[0]  # drop a possible time-of-day suffix
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def import_accrual_report_from_file(
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

    header_idx = _find_header_row(raw)
    if header_idx is None:
        result.errors.append(
            "Не найдена строка заголовков (ожидаются колонки 'Группа услуг' и 'Тип начисления') — "
            "ожидается отчёт «Начисления» из раздела Финансы личного кабинета Ozon"
        )
        return result

    header = raw.iloc[header_idx]
    columns: dict[str, int] = {}
    for idx, label in enumerate(header):
        text = clean_str(label)
        for key, expected in _REQUIRED_COLUMNS.items():
            if text == expected:
                columns[key] = idx

    missing = set(_REQUIRED_COLUMNS) - columns.keys()
    if missing:
        missing_labels = [_REQUIRED_COLUMNS[k] for k in sorted(missing)]
        result.errors.append(f"В файле отсутствуют обязательные колонки: {', '.join(missing_labels)}")
        return result

    sums: dict[tuple[date, str, str], float] = defaultdict(float)
    counts: dict[tuple[date, str, str], int] = defaultdict(int)
    unparseable_dates = 0

    for _, row in raw.iloc[header_idx + 1 :].iterrows():
        if row.isna().all():
            continue
        accrual_date = _parse_cell_date(row[columns["date"]])
        group = clean_str(row[columns["group"]])
        charge_type = clean_str(row[columns["type"]])
        amount = clean_number(row[columns["amount"]])
        if accrual_date is None:
            unparseable_dates += 1
            continue
        if not group or not charge_type or amount is None:
            continue
        result.fetched += 1
        key = (accrual_date, group, charge_type)
        sums[key] += amount
        counts[key] += 1

    if unparseable_dates:
        result.errors.append(f"Пропущено строк с нераспознанной датой начисления: {unparseable_dates}")

    if not sums:
        result.errors.append("В файле не нашлось ни одной строки с распознанными датой/суммой — нечего импортировать")
        return result

    existing = {
        (r.accrual_date, r.service_group, r.charge_type): r
        for r in db_session.query(AccrualReportDailyStatistic).filter(AccrualReportDailyStatistic.store_id == store_id).all()
    }
    for (accrual_date, group, charge_type), amount in sums.items():
        rows_count = counts[(accrual_date, group, charge_type)]
        row_obj = existing.get((accrual_date, group, charge_type))
        if row_obj is None:
            row_obj = AccrualReportDailyStatistic(
                store_id=store_id, accrual_date=accrual_date, service_group=group, charge_type=charge_type,
            )
            db_session.add(row_obj)
        row_obj.amount_rub = round(amount, 2)
        row_obj.rows_count = rows_count
        row_obj.source = "manual_xlsx_upload"
        result.created += 1

    return result
