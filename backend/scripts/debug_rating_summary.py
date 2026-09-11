"""One-off diagnostic script: probes Ozon Seller API's rating summary
method for "Локализация" (% локальных заказов) — the one indicator the
«РНП Товары» planner page (app.services.product_planner_service) currently
always shows as None (see that module's own docstring, and README's «РНП
Товары» section, both marked "not confirmed" as of 2026-09-11).

An Ozon support answer (2026-09-11) named "POST /v1/rating/" as the method
and "localization_index" as the field carrying it. That exact path is NOT
in this account's own full method list (docs/ozon-seller-api-methods.md
has "/v1/rating/index/fbs/*", "/v1/rating/history", "/v1/rating/summary" —
no bare "/v1/rating/") — "/v1/rating/summary" is the closest CONFIRMED
real method name and the most likely candidate for what the support answer
meant (imprecise/truncated paths from Ozon's own support chat have already
happened once this project, for the cash-flow-statement method). This
script tries that confirmed name first — do NOT assume "/v1/rating/" is
real without seeing what Ozon actually returns for it.

Also worth confirming/ruling out from the real response: rating summary is
normally an ACCOUNT-WIDE seller rating report, not per-SKU — the planner
page needs a PER-PRODUCT percentage ("Доля локальных продаж" per Ozon's
own «Локальность продаж» UI report). If localization_index turns out to be
a single account-level number with no product breakdown, it can only ever
populate the "Итого" row's Локализация tile, not each product's — that's a
real possibility this diagnostic should surface, not paper over.

Usage:

    docker compose exec app python backend/scripts/debug_rating_summary.py \\
        --store-id <id> > rating_summary_debug.txt
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
    args = parser.parse_args()

    db = SessionLocal()
    try:
        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == args.store_id).first()
        if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
            print("Для этого магазина не заданы ключи Ozon Seller API.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)
        print(f"Client-Id: {client_id}")

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            # Confirmed real method name (this account's own method list) —
            # tried first, empty body (most Ozon "summary"-style reports
            # take no filter at all).
            _try(client, "Rating summary (confirmed method name, empty body)", "/v1/rating/summary", {})

            # The support answer's literal (likely imprecise) path, in case
            # it really is a distinct method — costs nothing to also try.
            _try(client, "Rating (support-answer's literal path, empty body)", "/v1/rating/", {})
    finally:
        db.close()


if __name__ == "__main__":
    main()
