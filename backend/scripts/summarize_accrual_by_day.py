"""Focused diagnostic: computes and prints ONLY three numbers from
POST /v1/finance/accrual/by-day for one date — no raw JSON shown (see
probe_accrual_and_realization.py for the earlier structure-discovery
round, which is what confirmed the shape used here):

  1. Sum of total_amount.amount across every record for the day.
  2. The same sum broken down by accrued_category (how many distinct
     categories, and the sum for each).
  3. Total record count.

CONFIRMED 2026-09-13 (real account, "Комфорт дом"): each record has
{accrual_id, date, total_amount, accrued_category, posting} — total_amount
is itself an object with its own "amount" field. Paginates properly
(loops while a page returns a FULL page_size worth of records — the
top-level pagination field name itself, e.g. page_count/has_next, is
NOT confirmed, so this uses the same "did this page come back full"
heuristic rather than guessing a specific field) so a day with more than
one page of records isn't silently undercounted.

Tries the same two request-body candidates as probe_accrual_and_
realization.py, in the same order, since it's still not confirmed here
which one is the "real" one — whichever succeeds is used for every page.

Usage:
    docker compose exec app python backend/scripts/summarize_accrual_by_day.py \\
        --store-id <id> --date 2026-09-12
"""
from __future__ import annotations

import argparse
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
MAX_PAGES = 20  # runaway guard, not a confirmed cap


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
    """Same generic "first list of dicts" walker as probe_accrual_and_
    realization.py — the real top-level key holding the records array
    isn't confirmed, so this doesn't hardcode one."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return ".".join(path) or "(корень)", obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            found_path, found_list = _find_item_list(value, path + (key,))
            if found_list is not None:
                return found_path, found_list
    return None, None


def _to_num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


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
    parser.add_argument("--store-id", default=None, help="внутренний UUID магазина")
    parser.add_argument("--store-name", default=None, help="имя магазина — альтернатива --store-id")
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
            records = _fetch_all_records(client, args.date)

        by_category: dict[str, float] = {}
        total = 0.0
        for r in records:
            amount = _to_num((r.get("total_amount") or {}).get("amount"))
            category = r.get("accrued_category") or "(без категории)"
            by_category[category] = by_category.get(category, 0.0) + amount
            total += amount

        print("=" * 70)
        print(f"/v1/finance/accrual/by-day за {args.date} — магазин {store_id}")
        print("=" * 70)
        print(f"1) ИТОГО total_amount.amount по всем записям: {round(total, 2)}")
        print(f"2) Разбивка по accrued_category ({len(by_category)} категорий):")
        for category, amount in sorted(by_category.items(), key=lambda kv: -abs(kv[1])):
            print(f"    {category}: {round(amount, 2)}")
        print(f"3) Всего записей: {len(records)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
