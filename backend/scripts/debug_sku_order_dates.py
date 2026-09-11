"""One-off diagnostic script: investigates a per-SKU order-count discrepancy
reported by the seller (2026-09-11) — the «РНП Товары» page's "Заказы"
counts for SKU 3249061904 (Магазин "Комфорт дом") don't match Ozon's own
cabinet export, and most of the recent postings seem to be missing
entirely: the first version of this script (narrow ~14-day window) found
only 29 matching postings against ~86 in the seller's own export for
roughly the same period.

SUMMARY-ONLY output (the very first version dumped raw JSON per posting —
~5900 lines on a real account; the seller works from a server console with
no scroll-back or copy, so anything longer than one screen is useless to
her). This version adds three specific checks, none of which need the
seller to interpret raw JSON:

  1. **Real field names** — the previous version guessed the order/creation
     date lives in a field called `created_at`; it found that field on 0 of
     29 postings, meaning that guess was simply wrong (not that Ozon omits
     the date). This version prints the ACTUAL top-level field names Ozon
     sent on one real posting instead of guessing again — see
     app.services.ozon.schemas.OzonPostingItem's `extra="allow"`, which
     means every field Ozon sends is captured even though this app doesn't
     parse most of them.
  2. **Pagination, made visible** — _fetch_all_postings() (the same
     function app.services.order_daily_sync_service's real sync uses)
     loops pages via `offset`/`has_next` silently; this script logs each
     page's offset/rows-returned/has_next so a silent pagination cutoff (if
     that's what's losing 2/3 of the postings) is directly visible instead
     of inferred.
  3. **A widened re-query** — if Ozon's `filter.since`/`filter.to` filters
     by `in_process_at` (or another field that lags behind when the order
     was actually placed — the current working hypothesis from
     order_daily_sync_service's own docstring), postings placed inside the
     seller's reported window could simply fall OUTSIDE it once dated by
     whatever field Ozon actually filters on. This re-runs the same
     per-SKU count over a window widened 30 days back and 14 days forward
     and reports the total, for direct comparison against the narrow-window
     count.

Usage (inside the running container):

    docker compose exec app python backend/scripts/debug_sku_order_dates.py \\
        --store-id <id> --sku 3249061904

    # override the narrow window (default: 14 days back from --date-to):
    docker compose exec app python backend/scripts/debug_sku_order_dates.py \\
        --store-id <id> --sku 3249061904 --date-from 2026-08-28 --date-to 2026-09-11
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.encryption import decrypt_secret  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.ozon_credentials import OzonCredentials  # noqa: E402
from app.services.order_daily_sync_service import MAX_PAGES, PAGE_LIMIT, _parse_in_process_at  # noqa: E402
from app.services.ozon.client import OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402
from app.services.ozon.exceptions import OzonAPIError  # noqa: E402

def _fetch_with_page_log(label: str, fetch_fn, *, date_from: str, date_to: str) -> list:
    all_postings: list = []
    offset = 0
    for page_num in range(1, MAX_PAGES + 1):
        try:
            response = fetch_fn(date_from=date_from, date_to=date_to, offset=offset, limit=PAGE_LIMIT)
        except OzonAPIError as exc:
            print(f"  {label} — OzonAPIError на странице {page_num}: {exc}")
            break
        result = response.result
        if result is None:
            print(f"  {label} — страница {page_num}: пустой result, останавливаюсь")
            break
        postings = result if isinstance(result, list) else result.postings
        has_next = False if isinstance(result, list) else bool(result.has_next)
        print(f"  {label} — страница {page_num}: offset={offset} получено={len(postings)} has_next={has_next}")
        if not postings:
            break
        all_postings.extend(postings)
        if not has_next:
            break
        offset += PAGE_LIMIT
    return all_postings


def _count_matching(postings: list, sku: int) -> list:
    return [p for p in postings if any(prod.sku == sku for prod in p.products)]


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
    narrow_from_ts = f"{date_from.isoformat()}T00:00:00Z"
    narrow_to_ts = f"{date_to.isoformat()}T23:59:59Z"

    wide_from = date_from - timedelta(days=30)
    wide_to = date_to + timedelta(days=14)
    wide_from_ts = f"{wide_from.isoformat()}T00:00:00Z"
    wide_to_ts = f"{wide_to.isoformat()}T23:59:59Z"

    db = SessionLocal()
    try:
        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == args.store_id).first()
        if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
            print("Для этого магазина не заданы ключи Ozon Seller API.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)

        print(f"SKU: {args.sku} | узкое окно: {date_from} — {date_to} | сегодня: {today}")

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            print("Узкое окно, постранично:")
            fbo_narrow = _fetch_with_page_log("FBO", client.list_fbo_postings, date_from=narrow_from_ts, date_to=narrow_to_ts)
            fbs_narrow = _fetch_with_page_log("FBS", client.list_fbs_postings, date_from=narrow_from_ts, date_to=narrow_to_ts)

            print(f"Широкое окно ({wide_from} — {wide_to}), постранично:")
            fbo_wide = _fetch_with_page_log("FBO", client.list_fbo_postings, date_from=wide_from_ts, date_to=wide_to_ts)
            fbs_wide = _fetch_with_page_log("FBS", client.list_fbs_postings, date_from=wide_from_ts, date_to=wide_to_ts)
    finally:
        db.close()

    narrow_matching = _count_matching(fbo_narrow, args.sku) + _count_matching(fbs_narrow, args.sku)
    wide_matching = _count_matching(fbo_wide, args.sku) + _count_matching(fbs_wide, args.sku)

    print(f"\nВсего отправлений (все SKU) — узкое окно: FBO={len(fbo_narrow)} FBS={len(fbs_narrow)}")
    print(f"Всего отправлений (все SKU) — широкое окно: FBO={len(fbo_wide)} FBS={len(fbs_wide)}")
    print(f"С этим SKU — узкое окно: {len(narrow_matching)} | широкое окно: {len(wide_matching)}")

    if narrow_matching:
        example = narrow_matching[0]
        print("\nПоля одного реального отправления с этим SKU (все ключи, без значений):")
        print(f"  {sorted(example.model_dump().keys())}")

    if len(wide_matching) > len(narrow_matching):
        narrow_ids = {p.posting_number for p in narrow_matching}
        extra = [p for p in wide_matching if p.posting_number not in narrow_ids]
        outside_dates = sorted({_parse_in_process_at(p.in_process_at) for p in extra}, key=lambda d: (d is None, d))
        print(f"\nОтправления, которые нашлись в широком окне, но не в узком — их даты in_process_at: {outside_dates}")


if __name__ == "__main__":
    main()
