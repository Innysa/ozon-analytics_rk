"""Diagnostic: raw probe of POST /v1/product/rating-by-sku — UNVERIFIED
contract, never called before. Its name superficially resembles what we
need (per-SKU breakdown of something Ozon calls "рейтинг"), but Ozon's own
docs.ozon.ru sidebar (per earlier screenshots the user sent this project)
groups a SEPARATE "Контент-рейтинг" (content/card-completeness score —
photos, description, attributes) under "рейтинг", which is NOT the same
thing as «Локализация» / «Локальность продаж» (% of orders fulfilled from
a local warehouse) — that one is confirmed (README, 2026-09-11) to live
ONLY in /v1/rating/summary, store-wide, no SKU dimension. This script
exists to check that suspicion against the REAL response rather than
asserting it from the method name alone — if the response turns out to
carry a localization-shaped field, that changes the whole plan; if it's
content-rating as expected, that's useful to confirm and rule out too.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/probe_rating_by_sku.py \\
        --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \\
        --sku 2953864771 --sku 1492106823
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
from app.services.ozon.client import OzonAPIError, OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402

# Candidate request bodies, tried in order — same probing style as
# probe_stock_on_warehouses.py.
_CANDIDATE_BODIES = [
    {"skus": []},          # filled in with real SKUs below
    {"sku": []},
    {},
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--sku", action="append", default=[], required=True)
    args = parser.parse_args()

    skus = [int(s) for s in args.sku]
    _CANDIDATE_BODIES[0] = {"skus": skus}
    _CANDIDATE_BODIES[1] = {"sku": skus}

    db = SessionLocal()
    try:
        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == args.store_id).first()
        if not creds:
            print("Для этого магазина не заданы ключи Ozon.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            data = None
            last_error: Exception | None = None
            for body in _CANDIDATE_BODIES:
                try:
                    print(f"--- пробую тело запроса: {json.dumps(body, ensure_ascii=False)}")
                    data = client._post("/v1/product/rating-by-sku", body)  # noqa: SLF001 — deliberate raw probe
                    print("    успех!")
                    break
                except OzonAPIError as exc:
                    print(f"    ошибка: {exc}")
                    last_error = exc
            if data is None:
                print("\nНи одно из тестовых тел запроса не сработало.")
                if last_error:
                    raise last_error
                return
    finally:
        db.close()

    print("\n=== ПОЛНЫЙ СЫРОЙ ОТВЕТ ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
