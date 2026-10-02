"""Diagnostic: minimal, single-purpose probe of /v1/supply-order/bundle
and /v1/supply-order/details given an already-known bundle_id (pulled out
of a previous /v3/supply-order/get response by hand — see
probe_supply_order_details.py's own docstring for the full backstory).
Deliberately does NOT chain multiple Ozon calls like that script — kept
as small as possible to isolate where things go wrong.

Usage (inside the running container):

    docker compose exec app python backend/scripts/probe_supply_order_bundle.py --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf --bundle-id 01a0fb66-d5e4-7eec-b111-197a722a0561 --order-id 132107093
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.encryption import decrypt_secret  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.ozon_credentials import OzonCredentials  # noqa: E402
from app.services.ozon.client import OzonAPIError, OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--order-id", required=True, type=int)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == args.store_id).first()
        if not creds:
            print("Для этого магазина не заданы ключи Ozon.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            print("ready, calling /v1/supply-order/bundle")
            try:
                data = client._post(  # noqa: SLF001
                    "/v1/supply-order/bundle", {"bundle_ids": [args.bundle_id], "limit": 100},
                )
                print("success")
                print(json.dumps(data, ensure_ascii=False, indent=2)[:8000])
            except OzonAPIError as exc:
                print(f"error: {exc}")

            print("\ncalling /v1/supply-order/details")
            try:
                data = client._post("/v1/supply-order/details", {"order_id": args.order_id})  # noqa: SLF001
                print("success")
                print(json.dumps(data, ensure_ascii=False, indent=2)[:8000])
            except OzonAPIError as exc:
                print(f"error: {exc}")
    finally:
        db.close()
    print("done")


if __name__ == "__main__":
    main()
