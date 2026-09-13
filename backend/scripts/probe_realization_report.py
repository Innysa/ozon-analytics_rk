"""One-off diagnostic script: calls ONLY `POST /v2/finance/realization`
(Ozon's monthly "Отчёт о реализации товаров" — the official monthly
settlement report) for one store/month/year and prints the raw response.

This is a narrower, single-purpose version of
backend/scripts/debug_cash_flow_statement.py's own probe of this same
endpoint (see that script's own comments: a real account's error message
going from "invalid Period.From: value is required" to "invalid
GetRealizationReportRequestV2.Year: value must be inside range [2000,
9999]" when year/month were moved to top-level fields suggests THIS shape
is closer to correct — but that round never confirmed a full SUCCESS
response, only that the validation error changed). Kept separate from
that script (which also probes cash-flow-statement pagination, balance,
accrual, decompensation, compensation in the same run) so a request for
"just call /v2/finance/realization and show me the raw response" doesn't
also re-run several already-explored, unrelated probes and spend extra
calls against the same rate-limited account.

No parsing, no storage — this exists purely to capture Ozon's actual
response shape before anything gets built on top of it, same discipline
as every other debug_*/probe_*.py script in this directory.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/probe_realization_report.py \\
        --store-id <id> --month 9 --year 2026

    # or by name instead of the UUID:
    docker compose exec app python backend/scripts/probe_realization_report.py \\
        --store-name "Комфорт дом" --month 9 --year 2026
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None, help="внутренний UUID магазина")
    parser.add_argument("--store-name", default=None, help="имя магазина — альтернатива --store-id")
    parser.add_argument("--month", type=int, required=True, help="1-12")
    parser.add_argument("--year", type=int, required=True)
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

        body = {"year": args.year, "month": args.month}
        print("=" * 70)
        print(f"POST /v2/finance/realization — магазин {store_id}, месяц={args.month}, год={args.year}")
        print("request body:")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        print("=" * 70)

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            try:
                data = client.probe_finance_endpoint("/v2/finance/realization", body)
            except OzonAPIError as exc:
                print(f"OzonAPIError: {exc}")
                return
            except Exception as exc:  # noqa: BLE001 — diagnostic script, show everything
                print(f"Непредвиденная ошибка ({type(exc).__name__}): {exc}")
                return

        print("SUCCESS, сырой ответ целиком:")
        print(json.dumps(data, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
