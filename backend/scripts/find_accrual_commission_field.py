"""Diagnostic: finds WHICH field inside /v1/finance/accrual/by-day records
actually distinguishes "Вознаграждение Ozon" (Комиссия Ozon) from other
charge types — confirmed too coarse to find via `accrued_category`
(POSTING/NON_ITEM/ITEM only).

Ground truth (independently confirmed by the user from Ozon's own cabinet
AND the "Начисления" XLSX export, for store_id a586ccc5-6030-4ec9-b133-
da9de24dafcf on 2026-09-12 — see README's own accrual/by-day section):

    Вознаграждение Ozon      -389940.02 ₽   (кабинет: -389940 ₽)
    Услуги доставки           -36382.00 ₽   (кабинет:  -36382 ₽)
    Продвижение и реклама     -29135.11 ₽   (кабинет:  -29135 ₽)
    Продажи                   770288.00 ₽   (кабинет:  770288 ₽)
    Возвраты                   -3250.00 ₽   (кабинет:   -3250 ₽)

CONFIRMED 2026-09-14: this script's original (string field, numeric
field) pairing already found "Комиссия Ozon" exactly —
`posting.products[].commission.sale_commission.amount` summed across
every product in every record's posting for the day reproduces
-389 940.02 ₽ exactly (see AccrualDailyStatistic.commission_ozon_rub and
accrual_daily_sync_service._extract_commission_ozon_rub, now wired up).
"Продажи"/"Возвраты" are NOT confirmed yet — unlike commission, revenue
plausibly needs price×quantity (a product's raw `price` field alone,
summed per product, would rarely equal the day's whole "Продажи" total
without multiplying by how many units were sold), so this script ALSO
tries that: any dict with an integer-looking `quantity`-ish field has its
OTHER numeric fields multiplied by that quantity before being treated as
an amount candidate, in addition to the raw (unmultiplied) fields.

Rather than guess which API field/value corresponds to "Вознаграждение
Ozon"/"Продажи"/"Возвраты" (the mistake this project has repeatedly
caught itself making), this script does NOT hardcode a field name. It:

  1. Recursively walks every record fetched for the day (top level AND
     nested, e.g. inside `posting`, since accrued_category being too
     coarse doesn't rule out a same-level sibling field, and the finer
     breakdown might also be one level deeper).
  2. For every dict found anywhere, pairs every descriptive string field
     with every numeric-ish field in the SAME dict (a numeric-ish field
     is a plain number, a numeric string, or a dict with an "amount" key
     — same shape as `total_amount`) AND with every numeric field
     multiplied by a same-dict quantity-ish field, in case revenue needs
     price×quantity rather than a raw field.
  3. Groups and sums each such (string field, numeric field) pair's
     values across the whole day.
  4. Checks two ways whether a grouping explains the ground truth above:
     a DIRECT match (one single value's sum already equals a reference
     figure) and a SUBSET match (a handful of values, e.g. several
     finer "Тип начисления" rows, sum together to a reference figure —
     since the cabinet's "Группа услуг" the user confirmed against is
     coarser than individual charge types).

Prints ONLY condensed grouped sums and ✅/❌ verdicts — never a raw
per-record JSON dump (no scrollback on the target console, same
constraint as every other diagnostic script in this project). Prints a
one-line ✅/❌ SUMMARY per category FIRST, before any of the detailed
per-hit listings below it — a real day's data can make those detailed
sections long, and this project's console has hit scrollback limits
before (see probe_accrual_and_realization.py's own docstring), so the
summary line is what to check first if the console output looks cut off,
and redirecting to a file (as below) avoids the question entirely.

Usage (on the real server, against the real database) — reuses a
previously saved raw response instead of a fresh API call when
--json-file is given (e.g. the file probe_accrual_and_realization.py
already saved to /tmp/accrual_by_day_2026-09-12.json). ALWAYS redirect to
a file rather than reading the console directly — this console has no
scrollback, so a long run can look truncated even when it finished fine:

    docker compose exec app python backend/scripts/find_accrual_commission_field.py \\
        --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf --date 2026-09-12 \\
        > /tmp/find_commission_field.txt; cat /tmp/find_commission_field.txt

    docker compose exec app python backend/scripts/find_accrual_commission_field.py \\
        --json-file /tmp/accrual_by_day_2026-09-12.json --date 2026-09-12 \\
        > /tmp/find_commission_field.txt; cat /tmp/find_commission_field.txt
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.encryption import decrypt_secret  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.ozon_credentials import OzonCredentials  # noqa: E402
from app.models.store import Store  # noqa: E402
from app.services.ozon.client import OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402
from app.services.ozon.exceptions import OzonAPIError  # noqa: E402

PAGE_SIZE = 1000
MAX_PAGES = 50
TOLERANCE_RUB = 0.05
MAX_DISTINCT_VALUES_FOR_GROUPING = 40
MAX_SUBSET_SIZE = 8

# Confirmed 2026-09-12 for the one real account this has been checked
# against — used ONLY to auto-verify a candidate field, never fetched or
# guessed by this script.
REFERENCE_TOTALS_RUB = {
    "Вознаграждение Ozon": -389940.02,
    "Услуги доставки": -36382.00,
    "Продвижение и реклама": -29135.11,
    "Продажи": 770288.00,
    "Возвраты": -3250.00,
}

_EXCLUDED_KEYS = {
    "currency_code", "date", "posting_number", "order_id", "order_number",
    "sku", "offer_id", "accrual_id", "unit_number", "id",
}


def _resolve_store_id(db, *, store_id: str | None, store_name: str | None) -> str | None:
    if store_id:
        return store_id
    matches = db.query(Store).filter(Store.name.ilike(f"%{store_name}%")).all()
    if len(matches) == 1:
        print(f"Найден магазин: {matches[0].id} — {matches[0].name}")
        return matches[0].id
    if not matches:
        print(f"Магазин с именем, похожим на «{store_name}», не найден.")
        return None
    print(f"Найдено несколько магазинов, подходящих под «{store_name}» — уточните --store-id:")
    for m in matches:
        print(f"    {m.id}  —  {m.name}")
    return None


def _find_item_list(obj, path=()):
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return ".".join(path) or "(корень)", obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            found_path, found_list = _find_item_list(value, path + (key,))
            if found_list is not None:
                return found_path, found_list
    return None, None


def _fetch_all_records_from_api(client, date_str: str) -> list[dict]:
    all_records: list[dict] = []
    page = 1
    while page <= MAX_PAGES:
        data = client.get_accrual_by_day(day=date_str, page=page, page_size=PAGE_SIZE)
        _, items = _find_item_list(data)
        if not items:
            break
        all_records.extend(items)
        if len(items) < PAGE_SIZE:
            break
        page += 1
    return all_records


def _extract_amount(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    if isinstance(value, dict) and "amount" in value:
        return _extract_amount(value["amount"])
    return None


def _is_descriptive_string(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    stripped = value.replace(".", "", 1).replace("-", "", 1)
    return not stripped.isdigit()  # excludes bare numeric-looking strings (ids, amounts-as-strings)


def _is_quantity_key(key: str) -> bool:
    lowered = key.lower()
    return "quantity" in lowered or "qty" in lowered


def _normalize_path(path: tuple[str, ...]) -> tuple[str, ...]:
    return tuple("*" if seg.isdigit() else seg for seg in path)


def _walk_dicts(obj, path=()):
    """Recursively yields (normalized_path, dict) for every dict found
    anywhere inside obj, including inside lists — deliberately not
    assuming the breakdown lives at any particular known key."""
    if isinstance(obj, dict):
        yield _normalize_path(path), obj
        for key, value in obj.items():
            yield from _walk_dicts(value, path + (key,))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            yield from _walk_dicts(item, path + (str(index),))


def _collect_grouped_sums(records: list[dict]) -> tuple[dict[tuple, dict[str, float]], dict[tuple, float]]:
    """Returns (grouped_sums, ungrouped_sums).

    grouped_sums: for every (path, name_key, amount_key) triple found
    anywhere across all records, the sum of amount_key's value grouped by
    name_key's value — for a "Тип начисления"-level field that needs a
    companion category to explain a coarser "Группа услуг" total.

    ungrouped_sums: for every (path, amount_key) pair, the PLAIN total
    across every occurrence, with no grouping at all — this is how the
    CONFIRMED commission field actually resolved (summing `posting.
    products[].commission.sale_commission.amount` across everything
    already equalled the reference on its own, no companion category
    needed), so revenue is checked the same direct way first.

    A record can contribute to many different (path, key) pairs at once —
    this casts a wide net on purpose, since the right field isn't known
    yet."""
    grouped: dict[tuple, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    ungrouped: dict[tuple, float] = defaultdict(float)
    for record in records:
        for path, d in _walk_dicts(record):
            name_fields = [(k, v) for k, v in d.items() if k not in _EXCLUDED_KEYS and _is_descriptive_string(v)]
            amount_fields = [(k, _extract_amount(v)) for k, v in d.items() if k not in _EXCLUDED_KEYS]
            amount_fields = [(k, a) for k, a in amount_fields if a is not None]

            # Also try price×quantity: revenue plausibly needs a per-unit
            # field multiplied by how many units, not a raw field alone.
            quantity_fields = [(k, v) for k, v in amount_fields if _is_quantity_key(k)]
            multiplied_fields = [
                (f"{amount_key}*{qty_key}", amount_value * qty_value)
                for amount_key, amount_value in amount_fields
                for qty_key, qty_value in quantity_fields
                if amount_key != qty_key
            ]
            all_amount_fields = amount_fields + multiplied_fields

            for amount_key, amount_value in all_amount_fields:
                ungrouped[(path, amount_key)] += amount_value
            for name_key, name_value in name_fields:
                for amount_key, amount_value in all_amount_fields:
                    group_key = (path, name_key, amount_key)
                    grouped[group_key][name_value] += amount_value
    return grouped, ungrouped


def _check_ungrouped_matches(ungrouped_sums: dict[tuple, float]) -> list[tuple]:
    """Returns [(path, amount_key, category, total)] for every plain,
    ungrouped (path, amount_key) total that already equals (within
    TOLERANCE_RUB) one of the confirmed reference totals — no companion
    category field needed. Checked BEFORE the grouped/subset checks below
    since this is the simpler, more direct explanation."""
    hits = []
    for (path, amount_key), total in ungrouped_sums.items():
        for category, reference in REFERENCE_TOTALS_RUB.items():
            if abs(total - reference) <= TOLERANCE_RUB:
                hits.append((path, amount_key, category, total))
    return hits


def _check_direct_matches(grouped_sums: dict[tuple, dict[str, float]]) -> list[tuple]:
    """Returns [(group_key, name_value, matched_reference_category)] for
    every grouping where one single value's total already equals (within
    TOLERANCE_RUB) one of the confirmed reference totals."""
    hits = []
    for group_key, value_sums in grouped_sums.items():
        if len(value_sums) > MAX_DISTINCT_VALUES_FOR_GROUPING:
            continue
        for name_value, total in value_sums.items():
            for category, reference in REFERENCE_TOTALS_RUB.items():
                if abs(total - reference) <= TOLERANCE_RUB:
                    hits.append((group_key, name_value, category, total))
    return hits


def _check_subset_matches(grouped_sums: dict[tuple, dict[str, float]]) -> list[tuple]:
    """For groupings with a small enough number of distinct values, tries
    whether a SUBSET of them sums to a reference total — the cabinet's
    "Группа услуг" the user confirmed against may be coarser than
    whatever field this script finds (e.g. several individual "Тип
    начисления" rows rolling up into one "Группа услуг")."""
    hits = []
    for group_key, value_sums in grouped_sums.items():
        n = len(value_sums)
        if n < 2 or n > MAX_SUBSET_SIZE:
            continue
        items = list(value_sums.items())
        for size in range(2, n + 1):
            for combo in itertools.combinations(items, size):
                total = sum(v for _, v in combo)
                for category, reference in REFERENCE_TOTALS_RUB.items():
                    if abs(total - reference) <= TOLERANCE_RUB:
                        hits.append((group_key, tuple(name for name, _ in combo), category, total))
    return hits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--store-name", default=None)
    parser.add_argument("--date", required=True, help="ГГГГ-ММ-ДД")
    parser.add_argument("--json-file", default=None, help="переиспользовать уже сохранённый сырой ответ вместо нового запроса к Ozon")
    args = parser.parse_args()

    if args.json_file:
        data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
        _, records = _find_item_list(data)
        if not records:
            print(f"В файле {args.json_file} не найден список записей.")
            return
    else:
        if not args.store_id and not args.store_name:
            print("Укажите --store-id/--store-name или --json-file.")
            return
        db = SessionLocal()
        try:
            store_id = _resolve_store_id(db, store_id=args.store_id, store_name=args.store_name)
            if not store_id:
                return
            creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == store_id).first()
            if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
                print("Для этого магазина не заданы ключи Ozon Seller API.")
                return
            client_id = decrypt_secret(creds.client_id_encrypted)
            api_key = decrypt_secret(creds.api_key_encrypted)
            try:
                with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
                    records = _fetch_all_records_from_api(client, args.date)
            except OzonAPIError as exc:
                print(f"Ошибка Ozon: {exc}")
                return
        finally:
            db.close()

    print("=" * 70)
    print(f"Всего записей за {args.date}: {len(records)}")
    print("=" * 70)

    grouped_sums, ungrouped_sums = _collect_grouped_sums(records)
    print(f"Найдено кандидатов: {len(ungrouped_sums)} полей, {len(grouped_sums)} группировок по категориям")

    ungrouped_hits = _check_ungrouped_matches(ungrouped_sums)
    direct_hits = _check_direct_matches(grouped_sums)
    subset_hits = _check_subset_matches(grouped_sums)

    # Печатается СРАЗУ, до всех подробных списков ниже (которые для
    # реального дня могут быть длинными) — если консоль обрежет вывод,
    # эта сводка всё равно покажет, какие категории вообще нашлись, а не
    # только те, что попали на экран первыми.
    matched_categories = {category for _, _, category, _ in ungrouped_hits} | {category for _, _, category, _ in direct_hits} | {
        category for _, _, category, _ in subset_hits
    }
    print("\nСВОДКА по категориям (до подробностей ниже):")
    for category in REFERENCE_TOTALS_RUB:
        mark = "✅ найдено" if category in matched_categories else "❌ НЕ найдено"
        print(f"    {category}: {mark}")

    if ungrouped_hits:
        print("\n✅ ПРЯМЫЕ совпадения (простая сумма поля по всем записям = весь итог категории, без группировки):")
        for path, amount_key, category, total in ungrouped_hits:
            print(f"    {category} ({REFERENCE_TOTALS_RUB[category]} ₽) == {total} ₽")
            print(f"        путь={'.'.join(path) or '(корень)'}  поле-сумма={amount_key}")
    else:
        print("\n❌ Прямых совпадений без группировки не найдено.")

    if direct_hits:
        print("\n✅ Совпадения ЧЕРЕЗ группировку (одно значение категориального поля = весь итог категории):")
        for group_key, name_value, category, total in direct_hits:
            path, name_key, amount_key = group_key
            print(f"    {category} ({REFERENCE_TOTALS_RUB[category]} ₽) == {total} ₽")
            print(f"        путь={'.'.join(path) or '(корень)'}  поле-название={name_key}='{name_value}'  поле-сумма={amount_key}")
    else:
        print("\n❌ Совпадений через группировку (одно значение = вся категория) не найдено.")

    if subset_hits:
        print("\n✅ Совпадения ГРУППОЙ значений (несколько типов начисления суммарно = категория):")
        for group_key, names, category, total in subset_hits:
            path, name_key, amount_key = group_key
            print(f"    {category} ({REFERENCE_TOTALS_RUB[category]} ₽) == {total} ₽  (сложены {len(names)} значений)")
            print(f"        путь={'.'.join(path) or '(корень)'}  поле-название={name_key}  поле-сумма={amount_key}")
            print(f"        значения: {list(names)}")
    else:
        print("\n❌ Совпадений группой значений не найдено.")

    if not ungrouped_hits and not direct_hits and not subset_hits:
        print("\nНи одно найденное поле не объясняет ни одну из подтверждённых сумм — печатаю топ-10")
        print("группировок по количеству затронутых записей, для ручного разбора:")
        by_size = sorted(grouped_sums.items(), key=lambda kv: -len(kv[1]))[:10]
        for group_key, value_sums in by_size:
            path, name_key, amount_key = group_key
            print(f"\n    путь={'.'.join(path) or '(корень)'}  поле-название={name_key}  поле-сумма={amount_key}  (значений: {len(value_sums)})")
            for name_value, total in sorted(value_sums.items(), key=lambda kv: -abs(kv[1]))[:15]:
                print(f"        {name_value!r}: {round(total, 2)} ₽")


if __name__ == "__main__":
    main()
