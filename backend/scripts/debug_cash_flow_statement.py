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


def _try(client: OzonSellerClient, label: str, path: str, body: dict) -> dict | None:
    print("=" * 70)
    print(f"{label} — POST {path}")
    print("request body:")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    try:
        data = client.probe_finance_endpoint(path, body)
    except OzonAPIError as exc:
        print(f"{label} — OzonAPIError: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 — diagnostic script, show everything
        print(f"{label} — unexpected error ({type(exc).__name__}): {exc}")
        return None
    print(f"{label} — SUCCESS, raw response:")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:8000])
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД, по умолчанию — 30 дней назад")
    parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД, по умолчанию — сегодня")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    date_to = date.fromisoformat(args.date_to) if args.date_to else today
    # 30 days (not 7, as in the first round) — wide enough to see more than
    # one of Ozon's own internal cash-flow periods (the confirmed response
    # returned periods NOT aligned to the requested range's own boundaries —
    # e.g. a 2026-09-01..2026-09-06 period appeared inside a 2026-09-04..
    # 2026-09-10 request — so a wider window is needed to see the actual
    # periodization pattern, not guess it from 1-2 examples).
    date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=29)
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

            # CONFIRMED WORKING shape (round 1, 2026-09-10, 7-day window):
            # {"date": {"from": <ISO ts>, "to": <ISO ts>}, "page", "page_size"}
            # returns {"result": {"cash_flows": [{"period": {"id","begin",
            # "end"}, "orders_amount", "returns_amount", "commission_amount",
            # "services_amount", "item_delivery_and_return_amount",
            # "currency_code"}, ...], "page_count", "details": []}}. The
            # periods returned were NOT aligned to the requested date range
            # (a 09-01..09-06 period inside a 09-04..09-10 request) — this
            # run uses a 30-day window and loops pages to reveal the real
            # periodization pattern instead of guessing from 2 examples, and
            # adds with_details=True (unset in the confirmed call, so
            # "details" came back empty — untested whether this is what
            # unlocks a logistics/storage/penalties breakdown INSIDE
            # services_amount, which is currently just one lump sum).
            offset_page = 1
            while True:
                data = _try(
                    client, f"Cash flow statement (date range, with_details, page {offset_page})",
                    "/v1/finance/cash-flow-statement/list",
                    {"date": {"from": date_from_ts, "to": date_to_ts}, "page": offset_page, "page_size": 1000, "with_details": True},
                )
                if not data:
                    break
                result = data.get("result") or {}
                page_count = result.get("page_count") or 1
                if offset_page >= page_count or offset_page >= 10:  # 10 = runaway guard, not a confirmed cap
                    break
                offset_page += 1

            # The monthly shape failed validation ("invalid Period.From:
            # value is required") — the method wants date.from/date.to even
            # for a "monthly" call, so this second variant is dropped; kept
            # here as a comment so a future reader doesn't re-try it blind:
            #   {"date": {"year": ..., "month": ...}, ...} -> always fails.

            # Fallback candidate — long-stable Ozon monthly realization
            # report. Round 1's error ("invalid GetRealizationReportRequestV2
            # .Year: value must be inside range [2000, 9999]") for a value
            # that WAS 2026 suggests year/month are TOP-LEVEL fields, not
            # nested under "date" as first guessed — tried flat this round.
            _try(client, "Realization report v2 (top-level year/month)", "/v2/finance/realization", {"year": report_year, "month": report_month})

            # Accrual family. Round 1's /v1/finance/accrual/by-day error
            # ("invalid value for string field date") means "date" wants a
            # STRING there, not an object — tried as a plain ISO date this
            # round (guessing date_to; the field name/whether it wants a
            # range is still unconfirmed).
            _try(client, "Accrual by day (date as string)", "/v1/finance/accrual/by-day", {"date": date_to.isoformat(), "page": 1, "page_size": 1000})
            _try(client, "Accrual types (no filter)", "/v1/finance/accrual/types", {})
            # /v1/finance/accrual/postings needs specific posting_numbers
            # (confirmed: "PostingNumbers: value must contain between 1 and
            # 200 items" — round 1's date-range guess was wrong on its face,
            # not just unconfirmed) — not useful for a period-wide query
            # without already knowing which postings to ask about, so it's
            # not retried here.

            # Штрафы (fines/penalties): no item name resembling one has been
            # observed inside cash-flow-statement's services/delivery_services/
            # delivery_return item lists on this account. These two methods
            # were seen in this account's own method list (round 1's
            # permissions check) but never tried — их названия ("декомпенсация"/
            # "компенсация") — the closest remaining unexplored candidates for
            # a penalty-like category. Guessing the same {"date": {"from",
            # "to"}} shape cash-flow-statement uses, since it's the only
            # confirmed convention on this account for a period-based finance
            # report — if wrong, Ozon's own validation error will say what
            # field it actually wants (as it has for every other method here).
            _try(client, "Decompensation (date range guess)", "/v1/finance/decompensation", {"date": {"from": date_from_ts, "to": date_to_ts}, "page": 1, "page_size": 1000})
            _try(client, "Compensation (date range guess)", "/v1/finance/compensation", {"date": {"from": date_from_ts, "to": date_to_ts}, "page": 1, "page_size": 1000})
    finally:
        db.close()


if __name__ == "__main__":
    main()
