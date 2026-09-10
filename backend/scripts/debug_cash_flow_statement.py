"""One-off diagnostic script: probes candidate replacement methods for the
now-confirmed-obsolete `POST /v3/finance/transaction/list` (see
OzonSellerClient.list_finance_transactions()'s own docstring — a real
account got `HTTP 400 {"code": 9, "message": "obsolete method cannot be
used"}` calling it on 2026-09-10).

A real account's own Ozon Seller API key permissions listing (checked
2026-09-10, role Admin — full method access) confirmed these method NAMES
exist for that account's key:

    /v1/finance/balance
    /v1/finance/accrual/postings
    /v1/finance/accrual/by-day
    /v1/finance/accrual/types
    /v1/finance/realization/posting
    /v1/finance/realization/by-day
    /v2/finance/realization
    /v1/finance/cash-flow-statement/list   <- user's top pick: by name and
                                               Ozon convention, this is where
                                               logistics/storage/commission/
                                               penalties/other deductions
                                               usually get aggregated in one
                                               report (ДДС = "Отчёт о движении
                                               денежных средств")
    /v1/finance/products/buyout
    /v1/finance/decompensation
    /v1/finance/compensation
    /v1/finance/mutual-settlement
    /v1/finance/document-b2b-sales(/json)

That listing confirms the method NAMES exist for this account — it says
NOTHING about the request/response CONTRACT (body fields, pagination style,
date format). This script tries several plausible request bodies against
the most promising candidates (informed by conventions already CONFIRMED
elsewhere in this codebase — e.g. the old v3 method's {"date": {"from",
"to"}, "page", "page_size"} shape, and Ozon's long-stable /v2/finance/
realization {"date": {"year", "month"}} monthly-report convention) and
prints whatever Ozon actually says back — a working response, or a
validation error naming the real required fields (Ozon's error messages
have repeatedly been informative enough on their own in this project, e.g.
"Request validation error: invalid ReviewListRequest.Limit: value must be
inside range [20, 100]"). Do NOT build a sync/scheduler/UI on top of any of
this until a response has been captured and confirmed stable.

Given a lost example already happened once with debug_orders_finance_api.py
(terminal scrollback, not a re-run), redirect output to a file rather than
copy-pasting from the terminal:

    docker compose exec app python backend/scripts/debug_cash_flow_statement.py \\
        --store-id <id> > cash_flow_debug.txt
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


def _try(client: OzonSellerClient, label: str, path: str, body: dict) -> None:
    print("=" * 70)
    print(f"{label} — POST {path}")
    print("request body:")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    try:
        data = client.probe_finance_endpoint(path, body)
    except OzonAPIError as exc:
        print(f"{label} — OzonAPIError: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 — diagnostic script, show everything
        print(f"{label} — unexpected error ({type(exc).__name__}): {exc}")
        return
    print(f"{label} — SUCCESS, raw response:")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:8000])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД, по умолчанию — 7 дней назад")
    parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД, по умолчанию — сегодня")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    date_to = date.fromisoformat(args.date_to) if args.date_to else today
    date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=6)
    date_from_ts = f"{date_from.isoformat()}T00:00:00Z"
    date_to_ts = f"{date_to.isoformat()}T23:59:59Z"
    # /v2/finance/realization and (guessed) cash-flow-statement are usually
    # calendar-month reports, not arbitrary ranges — use the month date_to falls in.
    report_year, report_month = date_to.year, date_to.month

    db = SessionLocal()
    try:
        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == args.store_id).first()
        if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
            print("Для этого магазина не заданы ключи Ozon Seller API.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)
        print(f"Client-Id: {client_id}")
        print(f"Период (для дневных методов): {date_from_ts} — {date_to_ts}")
        print(f"Месяц (для помесячных методов): {report_year}-{report_month:02d}")

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            # Cheapest possible sanity check that the Finance scope on this
            # key actually works at all, before trying anything shaped.
            _try(client, "Balance", "/v1/finance/balance", {})

            # User's top candidate — ДДС report. Two shapes tried since the
            # convention (monthly vs. date-range) isn't confirmed for this
            # specific (newer) method.
            _try(
                client, "Cash flow statement (monthly)", "/v1/finance/cash-flow-statement/list",
                {"date": {"year": report_year, "month": report_month}, "page": 1, "page_size": 1000, "with_details": True},
            )
            _try(
                client, "Cash flow statement (date range)", "/v1/finance/cash-flow-statement/list",
                {"date": {"from": date_from_ts, "to": date_to_ts}, "page": 1, "page_size": 1000},
            )

            # Fallback candidate — long-stable Ozon monthly realization report.
            _try(
                client, "Realization report v2 (monthly)", "/v2/finance/realization",
                {"date": {"year": report_year, "month": report_month}},
            )

            # Accrual family — "by-day" sounds most directly useful for a
            # daily dashboard; "postings" and "types" are reference-shaped
            # guesses (types especially might not need a body/date at all).
            _try(
                client, "Accrual by day", "/v1/finance/accrual/by-day",
                {"date": {"from": date_from_ts, "to": date_to_ts}, "page": 1, "page_size": 1000},
            )
            _try(client, "Accrual types (no filter)", "/v1/finance/accrual/types", {})
            _try(
                client, "Accrual postings (date range guess)", "/v1/finance/accrual/postings",
                {"date": {"from": date_from_ts, "to": date_to_ts}, "page": 1, "page_size": 1000},
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
