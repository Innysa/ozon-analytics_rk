"""Diagnostic: reads an ALREADY-SAVED raw response from /v1/finance/
realization/by-day (as saved by probe_realization_by_day_and_accrual_
reference.py to e.g. /tmp/realization_by_day_2026-09-12.json) and checks
whether summing candidate per-row fields reproduces the CONFIRMED ground
truth for "Продажи"/"Возвраты" (see find_accrual_commission_field.py's
own docstring for where these numbers come from and which store/date).

CONTEXT 2026-09-19: this endpoint's real per-row shape (CONFIRMED on a
real account, body {"year", "month", "day"} as separate top-level ints —
Ozon's own error named the "Day" field) turned out to have the SAME item
shape as the already-partially-confirmed monthly /v2/finance/realization
report: {item: {name, offer_id, barcode, sku}, seller_price_per_instance,
delivery_commission, return_commission, commission_ratio, rowNumber} —
one row per delivered UNIT (rowNumber suggests no separate quantity
field is needed). This script sums the plausible revenue candidates
(seller_price_per_instance alone, and delivery_commission's own numeric
sub-fields if present) and prints a condensed ✅/❌ against the confirmed
reference totals — no guessing which one is right, the arithmetic either
matches or it doesn't.

No API call — reads the local JSON file only, so re-running this costs
nothing and doesn't touch Ozon's rate limit.

Usage (on the real server):

    docker compose exec app python backend/scripts/check_realization_by_day_revenue_field.py --file /tmp/realization_by_day_2026-09-12.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

TOLERANCE_RUB = 0.05

# Identifier-shaped fields that happen to be numeric but aren't amounts —
# summing them produces meaningless noise in the output, not a real candidate.
_EXCLUDED_FIELDS = {"item.sku", "item.barcode", "rowNumber", "commission_ratio"}

REFERENCE_TOTALS_RUB = {
    "Вознаграждение Ozon": -389940.02,
    "Услуги доставки": -36382.00,
    "Продвижение и реклама": -29135.11,
    "Продажи": 770288.00,
    "Возвраты": -3250.00,
}


def _to_num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _find_item_list(obj, path=()):
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return ".".join(path) or "(корень)", obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            found_path, found_list = _find_item_list(value, path + (key,))
            if found_list is not None:
                return found_path, found_list
    return None, None


def _flatten_numeric_fields(row: dict, prefix: str = "") -> dict[str, float]:
    """Flattens every numeric (or numeric-string) leaf field in a row into
    {"dotted.path": value} — including nested dicts like delivery_commission,
    without assuming which sub-field (if any) is the one that matters."""
    flat: dict[str, float] = {}
    for key, value in row.items():
        full_key = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten_numeric_fields(value, prefix=f"{full_key}."))
        elif isinstance(value, bool):
            continue
        elif isinstance(value, (int, float)):
            flat[full_key] = float(value)
        elif isinstance(value, str):
            try:
                flat[full_key] = float(value)
            except ValueError:
                pass
    return flat


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, help="путь к уже сохранённому JSON-файлу")
    args = parser.parse_args()

    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    _, rows = _find_item_list(data)
    if not rows:
        print("В файле не найден список позиций.")
        return
    print(f"Всего позиций: {len(rows)}")

    totals: dict[str, float] = defaultdict(float)
    seen_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        flat = _flatten_numeric_fields(row)
        for field, value in flat.items():
            if field in _EXCLUDED_FIELDS:
                continue
            totals[field] += value
            seen_counts[field] += 1

    print("\nСуммы по каждому числовому полю (путь: сумма, в скольких позициях встретилось):")
    matched = set()
    for field, total in sorted(totals.items(), key=lambda kv: -abs(kv[1])):
        marker = ""
        for category, reference in REFERENCE_TOTALS_RUB.items():
            if abs(total - reference) <= TOLERANCE_RUB:
                marker = f"  ✅ == {category} ({reference} ₽)"
                matched.add(category)
        print(f"    {field}: {round(total, 2)}  (в {seen_counts[field]} из {len(rows)} позиций){marker}")

    # ПРОИЗВОДНЫЕ кандидаты — не просто сумма поля, а сумма ПРОИЗВЕДЕНИЯ
    # цены за единицу (seller_price_per_instance) на количество единиц
    # (delivery_commission.quantity / return_commission.quantity), плюс
    # разница двух сумм (комиссия Ozon по доставке минус её возврат по
    # возвращённым товарам). Обычное суммирование одного поля не могло
    # найти это, так как это не сумма ОДНОГО поля, а комбинация.
    weighted_revenue = 0.0
    weighted_returns = 0.0
    delivery_standard_fee_total = 0.0
    return_standard_fee_total = 0.0
    for row in rows:
        price = _to_num(row.get("seller_price_per_instance"))
        dc = row.get("delivery_commission") or {}
        rc = row.get("return_commission") or {}
        weighted_revenue += price * _to_num(dc.get("quantity"))
        weighted_returns += price * _to_num(rc.get("quantity"))
        delivery_standard_fee_total += _to_num(dc.get("standard_fee"))
        return_standard_fee_total += _to_num(rc.get("standard_fee"))

    derived_candidates = {
        "Продажи = Σ(seller_price_per_instance × delivery_commission.quantity)": weighted_revenue,
        "Возвраты = -Σ(seller_price_per_instance × return_commission.quantity)": -weighted_returns,
        "Вознаграждение Ozon = -(Σdelivery_commission.standard_fee − Σreturn_commission.standard_fee)": (
            -(delivery_standard_fee_total - return_standard_fee_total)
        ),
    }
    print("\nПроизводные кандидаты (произведения/разности полей, не просто суммы):")
    for label, total in derived_candidates.items():
        marker = ""
        for category, reference in REFERENCE_TOTALS_RUB.items():
            if abs(total - reference) <= TOLERANCE_RUB:
                marker = f"  ✅ == {category} ({reference} ₽)"
                matched.add(category)
        print(f"    {label}: {round(total, 2)}{marker}")

    print("\n" + "=" * 70)
    print("СВОДКА по подтверждённым категориям:")
    for category in REFERENCE_TOTALS_RUB:
        mark = "✅ найдено" if category in matched else "❌ НЕ найдено"
        print(f"    {category}: {mark}")


if __name__ == "__main__":
    main()
