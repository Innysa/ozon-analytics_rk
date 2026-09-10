"""One-off diagnostic script: calls Ozon Seller API's postings/finance
endpoints directly for one store and prints a CONDENSED look at Ozon's raw
JSON response — counts plus one full example per distinct shape, not every
row — specifically so the output stays small enough to copy out of a
terminal by hand (a full-length dump of a real account's data was ~1860
lines, too much to paste back reliably). Given how easy it is to lose part
of a long terminal dump on copy/paste (this happened at least once with
this exact script — see below), prefer redirecting stdout to a file over
copy-pasting from the terminal, e.g.:

    docker compose exec app python backend/scripts/debug_orders_finance_api.py \\
        --store-id <id> > finance_debug.txt

list_fbo_postings/list_fbs_postings (OzonSellerClient) are CONFIRMED and in
production use (app.services.order_daily_sync_service) — this script's
FBO/FBS calls below still exist mainly to inspect a fresh account's raw
shape when debugging something unrelated, not because their contract is in
doubt.

list_finance_transactions (`POST /v3/finance/transaction/list`) is
CONFIRMED OBSOLETE as of 2026-09-10 — Ozon now returns `HTTP 400 {"code":
9, "message": "obsolete method cannot be used"}` for it on a real account.
This explains why an earlier run of this script never captured a `type=
"other"` example: the method was already dead, not a copy-paste accident.
The finance-transactions call below is kept only so this script keeps
surfacing that exact error clearly (instead of silently skipping) until a
confirmed replacement method is wired up here — do not build a sync/
scheduler/UI on top of a guessed replacement. Building on a guessed
response shape is exactly what caused two real bugs earlier in this
project (a wrong CSV column name, a wrong JSON key) — this script exists so
that doesn't happen again: run it, then send the printed output back
before any parsing/storage logic gets written.

Usage (inside the running container):

    docker compose exec app python backend/scripts/debug_orders_finance_api.py --store-id <id>

    # narrow the date range (default: last 7 days) — a wider range makes it
    # more likely to see every distinct operation `type` at least once, but
    # doesn't otherwise change how much gets printed (still one example per
    # distinct shape, however many rows were actually found):
    docker compose exec app python backend/scripts/debug_orders_finance_api.py \\
        --store-id <id> --date-from 2026-08-01 --date-to 2026-09-07

    # skip one or more of the three calls, e.g. if FBO/FBS doesn't apply to
    # this store's fulfillment model:
    docker compose exec app python backend/scripts/debug_orders_finance_api.py \\
        --store-id <id> --skip-fbo --skip-fbs

Prints, per endpoint: the exact request body sent, the total row count, and
then ONE complete example row per distinct posting/operation "shape" found
— for postings that's just the first one overall (there's no natural
sub-type), for finance transactions that's one example per distinct
`type` value (orders/returns/services/compensation/other/...), so a sale
and a return are never confused for the same shape even if both exist in
the period. Or the exact error Ozon returned, if any — a 404/403 here
usually means the method needs a different Ozon plan/permission, which is
itself useful information, not just a failure to shrug off.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.encryption import decrypt_secret  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.ozon_credentials import OzonCredentials  # noqa: E402
from app.services.ozon.client import OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402
from app.services.ozon.exceptions import OzonAPIError  # noqa: E402

# Cap on distinct finance operation `type` examples printed — a real
# account is expected to have well under this many distinct types; the cap
# just guards against the output blowing back up if that assumption is
# wrong.
_MAX_FINANCE_TYPE_EXAMPLES = 12


def _print_postings_summary(label: str, body: dict, fn) -> None:
    print("=" * 70)
    print(f"{label} — request body:")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    try:
        response = fn()
    except OzonAPIError as exc:
        print(f"{label} — OzonAPIError: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 — diagnostic script, show everything
        print(f"{label} — unexpected error ({type(exc).__name__}): {exc}")
        return

    result = response.result
    if result is None:
        print(f"{label} — ответ не похож ни на один ожидаемый формат (result отсутствует). Полный ответ:")
        print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2)[:5000])
        return

    postings = result if isinstance(result, list) else result.postings
    print(f"{label} — всего найдено записей: {len(postings)}")
    if not postings:
        print(f"{label} — нет ни одной записи за этот период.")
        return
    print(f"{label} — первая запись целиком (все поля):")
    print(json.dumps(postings[0].model_dump(mode="json"), ensure_ascii=False, indent=2))


def _print_finance_summary(label: str, body: dict, fn) -> None:
    print("=" * 70)
    print(f"{label} — request body:")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    try:
        response = fn()
    except OzonAPIError as exc:
        print(f"{label} — OzonAPIError: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        print(f"{label} — unexpected error ({type(exc).__name__}): {exc}")
        return

    if response.result is None:
        print(f"{label} — ответ не похож на ожидаемый формат (result отсутствует). Полный ответ:")
        print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2)[:5000])
        return

    operations = response.result.operations
    print(f"{label} — всего найдено операций: {len(operations)}")
    if not operations:
        print(f"{label} — нет ни одной операции за этот период.")
        return

    by_type: dict[str, list] = {}
    for op in operations:
        by_type.setdefault(op.type or "(без type)", []).append(op)

    counts = {t: len(ops) for t, ops in by_type.items()}
    print(f"{label} — разбивка по полю type: {json.dumps(counts, ensure_ascii=False)}")

    for t, ops in list(by_type.items())[:_MAX_FINANCE_TYPE_EXAMPLES]:
        print(f"--- {label} — пример операции с type={t!r} (все поля) ---")
        print(json.dumps(ops[0].model_dump(mode="json"), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД, по умолчанию — 7 дней назад")
    parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД, по умолчанию — сегодня")
    parser.add_argument("--skip-fbo", action="store_true")
    parser.add_argument("--skip-fbs", action="store_true")
    parser.add_argument("--skip-finance", action="store_true")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    date_to = date.fromisoformat(args.date_to) if args.date_to else today
    date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=6)
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
        print(f"Период: {date_from_ts} — {date_to_ts}")

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            if not args.skip_fbo:
                _print_postings_summary(
                    "FBO postings (/v2/posting/fbo/list)",
                    {"filter": {"since": date_from_ts, "to": date_to_ts}, "limit": 1000},
                    lambda: client.list_fbo_postings(date_from=date_from_ts, date_to=date_to_ts),
                )
            if not args.skip_fbs:
                _print_postings_summary(
                    "FBS postings (/v3/posting/fbs/list)",
                    {"filter": {"since": date_from_ts, "to": date_to_ts}, "limit": 1000},
                    lambda: client.list_fbs_postings(date_from=date_from_ts, date_to=date_to_ts),
                )
            if not args.skip_finance:
                _print_finance_summary(
                    "Finance transactions (/v3/finance/transaction/list)",
                    {"filter": {"date": {"from": date_from_ts, "to": date_to_ts}, "transaction_type": "all"}, "page": 1, "page_size": 1000},
                    lambda: client.list_finance_transactions(date_from=date_from_ts, date_to=date_to_ts),
                )
    finally:
        db.close()


if __name__ == "__main__":
    main()
