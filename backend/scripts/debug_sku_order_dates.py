"""One-off diagnostic script: investigates a per-SKU daily order-count
discrepancy reported by the seller (2026-09-11) — the «РНП Товары» page's
"Заказы" counts for SKU 3249061904 (Магазин "Комфорт дом") don't match a
row-by-row export from Ozon's own cabinet, and the most recent days are
missing entirely.

SUMMARY-ONLY output (rewritten 2026-09-11 — the first version dumped raw
JSON per posting, which came out to ~5900 lines; the seller can only work
from the server console, with no scroll-back or copy, so anything longer
than a screen is useless to her). Prints only aggregated counts — no raw
postings.

Two working hypotheses from reading app.services.order_daily_sync_service
(NOT yet confirmed against real data — that's what this script is for):

  1. `aggregate_postings_by_day()`/`aggregate_postings_by_sku_and_day()`
     bucket every posting by `posting.in_process_at`'s DATE, and SILENTLY
     SKIP any posting where `in_process_at` is null. If Ozon only sets
     `in_process_at` once it starts actively processing an order (which
     can lag behind when the order was placed), very recent orders could
     have `in_process_at` still null at sync time — dropped entirely, not
     just miscounted. This would explain missing days that are otherwise
     fully in the past.
  2. Even where `in_process_at` IS set, its date might not match the date
     Ozon's own export attributes the order to (e.g. the export using
     order/creation date, `in_process_at` being when processing actually
     started, a day or more later) — reshuffling counts between
     neighboring days without any day being empty.

This script pulls the SAME FBO+FBS postings this app's own sync would
fetch (same CONFIRMED client methods, same `filter.since/to` window),
filters to just the reported SKU, and prints:
  - how many matching postings have an empty in_process_at
  - a by-date count using in_process_at, side by side with a by-date count
    using `created_at` (if Ozon sends that field at all — OzonPostingItem
    has `extra="allow"`, so it's captured even though nothing in this app
    currently reads it; a real example the seller sent earlier suggested
    the two fields might just match, but that was one posting, not a
    pattern — this checks it across everything in range)
  - a status breakdown (delivered/cancelled/unfinished)
  - how many postings have in_process_at and created_at landing on
    DIFFERENT dates

Usage (inside the running container):

    docker compose exec app python backend/scripts/debug_sku_order_dates.py \\
        --store-id <id> --sku 3249061904

    # widen/narrow the window (default: 14 days back from --date-to):
    docker compose exec app python backend/scripts/debug_sku_order_dates.py \\
        --store-id <id> --sku 3249061904 --date-from 2026-08-28 --date-to 2026-09-11
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.encryption import decrypt_secret  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.ozon_credentials import OzonCredentials  # noqa: E402
from app.services.order_daily_sync_service import _fetch_all_postings, _parse_in_process_at  # noqa: E402
from app.services.ozon.client import OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402
from app.services.ozon.exceptions import OzonAPIError  # noqa: E402

_EMPTY = "(пусто)"
_NO_FIELD = "(нет поля)"


def _parse_date_field(value: object) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _fetch_matching(fetch_fn, *, date_from: str, date_to: str, sku: int) -> tuple[int, list]:
    try:
        postings = _fetch_all_postings(fetch_fn, date_from=date_from, date_to=date_to)
    except OzonAPIError as exc:
        print(f"  OzonAPIError: {exc}")
        return 0, []
    matching = [p for p in postings if any(prod.sku == sku for prod in p.products)]
    return len(postings), matching


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--sku", required=True, type=int)
    parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД, по умолчанию — 14 дней назад от --date-to")
    parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД, по умолчанию — сегодня")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    date_to = date.fromisoformat(args.date_to) if args.date_to else today
    date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=14)
    date_from_ts = f"{date_from.isoformat()}T00:00:00Z"
    date_to_ts = f"{date_to.isoformat()}T23:59:59Z"

    db = SessionLocal()
    try:
        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == args.store_id).first()
        if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
            print("Для этого магазина не заданы ключи Ozon Seller API.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)

        print(f"SKU: {args.sku} | период: {date_from} — {date_to} | сегодня: {today}")

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            fbo_total, fbo_matching = _fetch_matching(client.list_fbo_postings, date_from=date_from_ts, date_to=date_to_ts, sku=args.sku)
            fbs_total, fbs_matching = _fetch_matching(client.list_fbs_postings, date_from=date_from_ts, date_to=date_to_ts, sku=args.sku)
    finally:
        db.close()

    print(f"FBO отправлений всего: {fbo_total}, с этим SKU: {len(fbo_matching)}")
    print(f"FBS отправлений всего: {fbs_total}, с этим SKU: {len(fbs_matching)}")

    matching = fbo_matching + fbs_matching
    if not matching:
        print("Отправлений с этим SKU в этом периоде не найдено.")
        return

    empty_in_process = sum(1 for p in matching if not p.in_process_at)
    no_created_field = sum(1 for p in matching if getattr(p, "created_at", None) is None)
    print(f"Отправлений с пустым in_process_at: {empty_in_process} из {len(matching)}")
    print(f"Отправлений без поля created_at вообще: {no_created_field} из {len(matching)}")

    by_in_process: Counter[str] = Counter()
    by_created: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    mismatch = 0

    for posting in matching:
        qty = sum(prod.quantity or 0 for prod in posting.products if prod.sku == args.sku)
        in_process_day = _parse_in_process_at(posting.in_process_at)
        created_day = _parse_date_field(getattr(posting, "created_at", None))

        by_in_process[str(in_process_day) if in_process_day else _EMPTY] += qty
        by_created[str(created_day) if created_day else (_NO_FIELD if getattr(posting, "created_at", None) is None else _EMPTY)] += qty
        by_status[posting.status or "(нет статуса)"] += qty
        if in_process_day and created_day and in_process_day != created_day:
            mismatch += 1

    all_days = sorted(set(by_in_process) | set(by_created), key=lambda d: (d in (_EMPTY, _NO_FIELD), d))
    print(f"\n{'Дата':<14}{'in_process_at':>15}{'created_at':>13}  (штук этого SKU)")
    for day in all_days:
        print(f"{day:<14}{by_in_process.get(day, 0):>15}{by_created.get(day, 0):>13}")

    print(f"\nОтправлений, где даты in_process_at и created_at НЕ совпадают: {mismatch}")

    print("\nПо статусу (штук этого SKU):")
    for status, qty in sorted(by_status.items()):
        print(f"  {status}: {qty}")


if __name__ == "__main__":
    main()
