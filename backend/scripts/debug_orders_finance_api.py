"""One-off diagnostic script: calls Ozon Seller API's postings/finance
endpoints directly for one store and prints the exact request and Ozon's
raw JSON response to stdout.

These three methods (OzonSellerClient.list_fbo_postings/list_fbs_postings/
list_finance_transactions) were added to eventually replace the manual
"Аналитика → Товары" CSV upload with automatic orders/revenue/logistics/
commission collection — but have NEVER been called against a real account.
Building a sync/scheduler/UI on top of a guessed response shape is exactly
what caused two real bugs earlier in this project (a wrong CSV column name,
a wrong JSON key) — this script exists so that doesn't happen a third time:
run it, then send the printed output back before any parsing/storage logic
gets written.

Usage (inside the running container):

    docker compose exec app python backend/scripts/debug_orders_finance_api.py --store-id <id>

    # narrow the date range (default: last 7 days) — a shorter range keeps
    # the printed output manageable for a first look:
    docker compose exec app python backend/scripts/debug_orders_finance_api.py \\
        --store-id <id> --date-from 2026-09-01 --date-to 2026-09-07

    # skip one or more of the three calls, e.g. if FBO/FBS doesn't apply to
    # this store's fulfillment model:
    docker compose exec app python backend/scripts/debug_orders_finance_api.py \\
        --store-id <id> --skip-fbo --skip-fbs

Prints, per endpoint: the exact request body sent, then Ozon's complete
raw JSON response (or the exact error Ozon returned, if any — a 404/403
here usually means the method needs a different Ozon plan/permission,
which is itself useful information, not just a failure to shrug off).
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


def _print_result(label: str, body: dict, fn) -> None:
    print("=" * 70)
    print(f"{label} — request body:")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print(f"{label} — raw response:")
    try:
        result = fn()
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)[:20000])
    except OzonAPIError as exc:
        print(f"OzonAPIError: {exc}")
    except Exception as exc:  # noqa: BLE001 — this is a diagnostic script, show everything
        print(f"Unexpected error ({type(exc).__name__}): {exc}")


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
                _print_result(
                    "FBO postings (/v2/posting/fbo/list)",
                    {"filter": {"since": date_from_ts, "to": date_to_ts}, "limit": 1000},
                    lambda: client.list_fbo_postings(date_from=date_from_ts, date_to=date_to_ts),
                )
            if not args.skip_fbs:
                _print_result(
                    "FBS postings (/v3/posting/fbs/list)",
                    {"filter": {"since": date_from_ts, "to": date_to_ts}, "limit": 1000},
                    lambda: client.list_fbs_postings(date_from=date_from_ts, date_to=date_to_ts),
                )
            if not args.skip_finance:
                _print_result(
                    "Finance transactions (/v3/finance/transaction/list)",
                    {"filter": {"date": {"from": date_from_ts, "to": date_to_ts}, "transaction_type": "all"}, "page": 1, "page_size": 1000},
                    lambda: client.list_finance_transactions(date_from=date_from_ts, date_to=date_to_ts),
                )
    finally:
        db.close()


if __name__ == "__main__":
    main()
