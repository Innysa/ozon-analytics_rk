"""One-off diagnostic script: investigates a per-SKU daily order-count
discrepancy reported by the seller (2026-09-11) — the «РНП Товары» page's
"Заказы" counts for SKU 3249061904 (Магазин "Комфорт дом", 1-10 сентября
2026) don't match a row-by-row export from Ozon's own cabinet, and the last
few days are missing entirely.

Two working hypotheses from reading app.services.order_daily_sync_service
(NOT yet confirmed against real data — that's what this script is for):

  1. `aggregate_postings_by_day()`/`aggregate_postings_by_sku_and_day()`
     bucket every posting by `posting.in_process_at`'s DATE, and SILENTLY
     SKIP any posting where `in_process_at` is null
     (`_parse_in_process_at()` returns None -> `if day is None: continue`).
     If `in_process_at` is only set once Ozon's warehouse starts actively
     processing an order (which can lag behind when the order was placed),
     very recent orders could have `in_process_at` still null at sync time
     — meaning they get dropped from every bucket entirely, not just
     miscounted. This would explain missing days that are otherwise fully
     in the past (8-10 Sept, "today" being 11 Sept).
  2. Even where `in_process_at` IS set, its DATE might not match the date
     Ozon's own "отчёт по отправлениям" export attributes the order to
     (e.g. the export might use order/creation date, `in_process_at` being
     when processing actually started, days later) — which would explain
     day-to-day reshuffling without any day being empty (an order counted
     under day 3 in Ozon's export showing up under day 4 here).

This script does NOT assume which (if either) is the real cause. It pulls
the SAME FBO+FBS postings this app's own sync would fetch (same client
methods, same `filter.since/to` window — deliberately widened beyond the
reported range to also catch any lag), filters to just the reported SKU,
and prints EVERY matching posting's raw JSON (not just the fields this app
currently parses — `extra="allow"` on OzonPostingItem means Ozon may be
sending more date-ish fields than `in_process_at` that this app silently
discards) plus a day-by-day count bucketed by `in_process_at` (INCLUDING a
separate count of postings with `in_process_at` null) so it's directly
comparable to the seller's own day-by-day table from Ozon's export.

Usage (inside the running container):

    docker compose exec app python backend/scripts/debug_sku_order_dates.py \\
        --store-id <id> --sku 3249061904 > sku_dates_debug.txt

    # widen/narrow the window (default: 21 days back from --date-to, to
    # give plenty of room for a processing lag to show up; default
    # --date-to is today):
    docker compose exec app python backend/scripts/debug_sku_order_dates.py \\
        --store-id <id> --sku 3249061904 --date-from 2026-08-20 --date-to 2026-09-11
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
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


def _investigate(label: str, fetch_fn, *, date_from: str, date_to: str, sku: int) -> None:
    print("=" * 70)
    print(f"{label} — filter.since={date_from} filter.to={date_to}")
    try:
        postings = _fetch_all_postings(fetch_fn, date_from=date_from, date_to=date_to)
    except OzonAPIError as exc:
        print(f"{label} — OzonAPIError: {exc}")
        return

    print(f"{label} — всего отправлений за период (все SKU): {len(postings)}")

    matching = [p for p in postings if any(prod.sku == sku for prod in p.products)]
    print(f"{label} — отправлений с SKU {sku}: {len(matching)}")
    if not matching:
        return

    by_day: dict[date | None, dict[str, int]] = defaultdict(lambda: {"delivered": 0, "cancelled": 0, "unfinished": 0, "postings": 0})
    for posting in matching:
        day = _parse_in_process_at(posting.in_process_at)
        qty = sum(prod.quantity or 0 for prod in posting.products if prod.sku == sku)
        status_bucket = "delivered" if posting.status == "delivered" else "cancelled" if posting.status == "cancelled" else "unfinished"
        by_day[day][status_bucket] += qty
        by_day[day]["postings"] += 1

    print(f"{label} — по дням (дата = in_process_at; 'None' = in_process_at ПУСТОЙ — такие отправления сейчас "
          f"НЕ попадают ни в один день, см. _parse_in_process_at()/aggregate_postings_by_day() в "
          f"order_daily_sync_service.py):")
    for day in sorted(by_day.keys(), key=lambda d: (d is None, d)):
        counts = by_day[day]
        total = counts["delivered"] + counts["cancelled"] + counts["unfinished"]
        print(
            f"    {day if day is not None else 'None (пусто)'}: отправлений={counts['postings']}, "
            f"штук всего(=Заказы в приложении)={total} "
            f"(delivered={counts['delivered']}, cancelled={counts['cancelled']}, unfinished={counts['unfinished']})"
        )

    print(f"\n{label} — полный raw JSON каждого отправления с этим SKU (для сверки с датой в отчёте Ozon):")
    for posting in matching:
        print("-" * 50)
        print(json.dumps(posting.model_dump(mode="json"), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--sku", required=True, type=int)
    parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД, по умолчанию — 21 день назад от --date-to")
    parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД, по умолчанию — сегодня")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    date_to = date.fromisoformat(args.date_to) if args.date_to else today
    date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=21)
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
        print(f"Client-Id: {client_id}")
        print(f"SKU: {args.sku}")
        print(f"Период запроса к Ozon: {date_from_ts} — {date_to_ts}")
        print(f"Сегодня (UTC): {today.isoformat()}")

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            _investigate("FBO postings (/v2/posting/fbo/list)", client.list_fbo_postings, date_from=date_from_ts, date_to=date_to_ts, sku=args.sku)
            _investigate("FBS postings (/v3/posting/fbs/list)", client.list_fbs_postings, date_from=date_from_ts, date_to=date_to_ts, sku=args.sku)
    finally:
        db.close()


if __name__ == "__main__":
    main()
