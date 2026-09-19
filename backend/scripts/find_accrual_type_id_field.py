"""Diagnostic: checks whether /v1/finance/accrual/by-day records carry an
INTEGER field that references POST /v1/finance/accrual/types' now-CONFIRMED
real dictionary (2026-09-19, on a real account: {"accrual_types": [{"id":
1, "name": "Acquiring", "description": "Эквайринг"}, ...]}, 124 entries) —
find_accrual_commission_field.py's earlier CLOSED search for revenue never
tried this, because it only ever paired STRING fields as the "categorical"
side of a (name, amount) grouping (`_is_descriptive_string` explicitly
excludes non-strings). If a record's per-line breakdown identifies its
Ozon type by a numeric id rather than a name string, that whole earlier
search structurally could not have found it.

This script:
  1. Fetches /v1/finance/accrual/types once (CONFIRMED to work with an
     empty body {}) and builds {id: description} in Russian, matching the
     "Тип начисления" values seen in a real "Начисления" XLSX cabinet
     export.
  2. Fetches every /v1/finance/accrual/by-day record for the given day
     (same confirmed pagination as summarize_accrual_by_day.py).
  3. Recursively walks every record for INTEGER fields whose value falls
     in the confirmed accrual_types id range (1..number of entries) —
     candidates for a type-id reference. Does NOT assume a field name
     ("type_id", "accrual_type_id", etc.) — casts a wide net exactly like
     this project's other exhaustive field searches.
  4. For each candidate (path, field name), groups the day's amounts by
     the dictionary DESCRIPTION that id resolves to, and checks — same
     two ways as find_accrual_commission_field.py — whether one value's
     sum (or a subset of values) reproduces one of the CONFIRMED reference
     totals for 12.09.2026 (see that script's own docstring for where
     these numbers come from and the ground-truth XLSX behind them).

Prints a condensed ✅/❌ summary first (this project's console has hit
scrollback limits before), then details. Never dumps a raw per-record
JSON dump.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/find_accrual_type_id_field.py \\
        --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf --date 2026-09-12
"""
from __future__ import annotations

import argparse
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

# Same ground truth as find_accrual_commission_field.py, for 12.09.2026,
# store_id a586ccc5-6030-4ec9-b133-da9de24dafcf.
REFERENCE_TOTALS_RUB = {
    "Вознаграждение Ozon": -389940.02,
    "Услуги доставки": -36382.00,
    "Продвижение и реклама": -29135.11,
    "Продажи": 770288.00,
    "Возвраты": -3250.00,
}

_EXCLUDED_INT_KEYS = {"page", "page_size", "quantity"}  # plausible false positives in the 1..124 range


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


def _to_num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _extract_amount(value):
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


def _normalize_path(path: tuple[str, ...]) -> tuple[str, ...]:
    return tuple("*" if seg.isdigit() else seg for seg in path)


def _fetch_accrual_types(client) -> dict[int, str]:
    data = client.probe_finance_endpoint("/v1/finance/accrual/types", {})
    _, items = _find_item_list(data)
    if not items:
        return {}
    return {item["id"]: item.get("description") or item.get("name") for item in items if "id" in item}


def _fetch_all_accrual_records(client, day_str: str) -> list[dict]:
    all_records: list[dict] = []
    seen_accrual_ids: set = set()
    page = 1
    while page <= MAX_PAGES:
        data = client.get_accrual_by_day(day=day_str, page=page, page_size=PAGE_SIZE)
        _, records = _find_item_list(data)
        if not records:
            break
        new_records = [r for r in records if r.get("accrual_id") not in seen_accrual_ids]
        if records and not new_records:
            break
        for r in new_records:
            if r.get("accrual_id") is not None:
                seen_accrual_ids.add(r["accrual_id"])
        all_records.extend(new_records)
        if len(records) < PAGE_SIZE:
            break
        page += 1
    return all_records


def _walk(obj, path=()):
    if isinstance(obj, dict):
        yield _normalize_path(path), obj
        for key, value in obj.items():
            yield from _walk(value, path + (key,))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            yield from _walk(item, path + (str(index),))


def _collect_candidate_id_fields(records: list[dict], max_id: int) -> set[tuple]:
    """Returns the set of (path, key) pairs where an integer value in
    [1, max_id] was seen anywhere across all records — candidates for a
    type-id reference into the accrual_types dictionary."""
    candidates: set[tuple] = set()
    for record in records:
        for path, d in _walk(record):
            for key, value in d.items():
                if key in _EXCLUDED_INT_KEYS:
                    continue
                if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= max_id:
                    candidates.add((path, key))
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--store-name", default=None)
    parser.add_argument("--date", required=True, help="ГГГГ-ММ-ДД")
    args = parser.parse_args()
    if not args.store_id and not args.store_name:
        print("Укажите --store-id или --store-name.")
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

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            try:
                types_by_id = _fetch_accrual_types(client)
            except OzonAPIError as exc:
                print(f"Не удалось получить /v1/finance/accrual/types: {exc}")
                return
            print(f"Справочник типов начислений: {len(types_by_id)} записей.")
            if not types_by_id:
                print("Справочник пуст — дальше проверять нечего.")
                return

            try:
                records = _fetch_all_accrual_records(client, args.date)
            except OzonAPIError as exc:
                print(f"Не удалось получить /v1/finance/accrual/by-day: {exc}")
                return
            print(f"Всего записей за {args.date}: {len(records)}")
    finally:
        db.close()

    max_id = max(types_by_id)
    candidates = _collect_candidate_id_fields(records, max_id)
    print(f"Найдено кандидатов на поле type-id (путь, ключ): {len(candidates)}")
    if not candidates:
        print("\n❌ Ни одно целочисленное поле нигде в записях не похоже на id из справочника типов начислений.")
        print("Вывод: числового type-id, ссылающегося на /v1/finance/accrual/types, в accrual/by-day НЕТ.")
        return

    matched_categories: set[str] = set()
    for path, key in sorted(candidates):
        sums: dict[str, float] = defaultdict(float)
        for record in records:
            for record_path, d in _walk(record):
                if record_path != path or key not in d:
                    continue
                type_id = d[key]
                description = types_by_id.get(type_id, f"(неизвестный id={type_id})")
                # Sum every numeric-ish sibling field in the SAME dict as a candidate amount.
                for amount_key, amount_value in d.items():
                    if amount_key == key:
                        continue
                    amount = _extract_amount(amount_value)
                    if amount is not None:
                        sums[f"{description} [{amount_key}]"] += amount

        if not sums:
            continue
        print(f"\nПоле-кандидат: путь={'.'.join(path) or '(корень)'}  ключ={key!r}")
        for label, total in sorted(sums.items(), key=lambda kv: -abs(kv[1])):
            marker = ""
            for category, reference in REFERENCE_TOTALS_RUB.items():
                if abs(total - reference) <= TOLERANCE_RUB:
                    marker = f"  ✅ == {category} ({reference} ₽)"
                    matched_categories.add(category)
            print(f"    {label}: {round(total, 2)} ₽{marker}")

    print("\n" + "=" * 70)
    print("СВОДКА по подтверждённым категориям:")
    for category in REFERENCE_TOTALS_RUB:
        mark = "✅ найдено" if category in matched_categories else "❌ НЕ найдено"
        print(f"    {category}: {mark}")


if __name__ == "__main__":
    main()
