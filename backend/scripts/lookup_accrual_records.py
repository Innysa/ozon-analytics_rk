"""Diagnostic: fetches ALL /v1/finance/accrual/by-day records for one date
(same confirmed request shape as summarize_accrual_by_day.py), then prints
the FULL raw content of ONLY the records whose accrual_id matches one of
the given --accrual-id values — built to correlate specific, already-known
lines from a real Ozon "Начисления" XLSX export against the API's own
record shape.

Why this exists: the XLSX export has a per-LINE breakdown ("Группа услуг" /
"Тип начисления" — e.g. "Вознаграждение Ozon" / "Вознаграждение за
продажу"), but the API so far has only shown a coarse accrued_category
(POSTING/NON_ITEM/ITEM per the user's own real output) — not enough to
isolate "Комиссия Ozon" from everything else. Critically, the SAME
accrual_id can appear on MULTIPLE XLSX rows with DIFFERENT "Группа услуг"
(e.g. one accrual_id had both a "Продажи" row and a "Вознаграждение Ozon"
row) — so it's not yet confirmed whether the API's total_amount for that
accrual_id is already a NETTED total across everything under it, or
whether the line-level breakdown lives somewhere inside the still-
unexamined "posting" field. This script exists to answer exactly that,
for a small, targeted set of accrual_ids instead of dumping the whole
day (see summarize_accrual_by_day.py for why that's avoided).

Usage (on the real server, against the real database) — pass one
--accrual-id per ID you want to inspect, ideally one whose XLSX rows
are ALL the same "Группа услуг" and one whose XLSX rows SPAN more than
one, to see both cases:

    docker compose exec app python backend/scripts/lookup_accrual_records.py \\
        --store-id <id> --date 2026-09-12 \\
        --accrual-id 0153138912-0063-1 --accrual-id 34144030 --accrual-id 2000065305252
"""
from __future__ import annotations

import argparse
import json
import sys
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
MAX_PAGES = 20


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


def _fetch_all_records(client, date_str: str) -> list[dict]:
    bodies = [
        lambda page: {"date": date_str, "page": page, "page_size": PAGE_SIZE},
        lambda page: {"date": {"from": date_str, "to": date_str}, "page": page, "page_size": PAGE_SIZE},
    ]
    last_error: Exception | None = None
    for build_body in bodies:
        try:
            first_page = client.probe_finance_endpoint("/v1/finance/accrual/by-day", build_body(1))
        except OzonAPIError as exc:
            last_error = exc
            continue
        _, items = _find_item_list(first_page)
        if items is None:
            last_error = RuntimeError("Ответ получен, но список записей внутри не найден")
            continue
        all_records = list(items)
        page = 1
        while len(items) == PAGE_SIZE and page < MAX_PAGES:
            page += 1
            next_page_data = client.probe_finance_endpoint("/v1/finance/accrual/by-day", build_body(page))
            _, items = _find_item_list(next_page_data)
            if not items:
                break
            all_records.extend(items)
        return all_records
    raise last_error or RuntimeError("Не удалось получить данные ни одним из вариантов запроса")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--store-name", default=None)
    parser.add_argument("--date", required=True, help="ГГГГ-ММ-ДД")
    parser.add_argument("--accrual-id", action="append", required=True, dest="accrual_ids", help="можно указывать несколько раз")
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
            records = _fetch_all_records(client, args.date)

        print("=" * 70)
        print(f"Всего записей за {args.date}: {len(records)}")
        print("=" * 70)

        wanted = set(args.accrual_ids)
        found_ids = set()
        for r in records:
            rid = str(r.get("accrual_id"))
            if rid in wanted:
                found_ids.add(rid)
                print(f"\n--- accrual_id={rid} — ПОЛНАЯ запись ---")
                print(json.dumps(r, ensure_ascii=False, indent=2, default=str))

        missing = wanted - found_ids
        if missing:
            print(f"\nНЕ найдено в ответе за эту дату: {sorted(missing)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
