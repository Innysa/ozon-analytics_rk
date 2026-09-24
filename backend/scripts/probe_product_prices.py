"""Diagnostic: raw probe of POST /v5/product/info/prices — UNVERIFIED
contract, never called before in this project (catalogued but unused, see
docs/ozon-seller-api-methods.md's «Товары» section).

Written 2026-09-24 after the user pointed out «СПП (расчёт)» on «РНП»/
«РНП Товары» reads far too low (~5%) compared to what she expects (her own
external tool shows 10-42% for the same SKU, and Ozon's own real formula —
see README — needs the seller's price AT THE TIME of the order, which this
app currently approximates with Product.price_rub from /v3/product/info/
list's "price" field). That field's own official description (per public
docs, since docs.ozon.ru is unreachable from this sandbox — see this repo's
own docs/ozon-seller-api-methods.md) is "price WITH discounts already
applied" — i.e. close to what the buyer pays, NOT a pre-SPP reference —
which would explain why comparing it against the order's own paid price
gives a near-zero (or occasionally negative) result: both sides of the
subtraction are close to the same number.

/v5/product/info/prices is a SEPARATE, dedicated pricing endpoint (per
public docs/third-party SDKs) that exposes several more price fields:
min_price, marketing_seller_price, price_indexes, old_price, retail_price,
net_price — one of these might be the actual pre-SPP reference. This probe
prints the FULL raw response for one product so those fields can be read
off directly and matched against the seller's own cabinet screenshot
(«Предельная цена без акций», «Ограничение для акций и стратегий», etc.)
rather than guessed at.

Tries a few candidate request bodies (filter key naming isn't confirmed)
and, if the endpoint path itself turns out wrong, that will surface as an
Ozon error naming the actual issue rather than a silent guess.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/probe_product_prices.py \\
        --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf --sku 3195982153
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
from app.models.product import Product  # noqa: E402
from app.services.ozon.client import OzonAPIError, OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402


def _candidate_bodies(*, offer_id: str | None, product_id: int | None, sku: str) -> list[dict]:
    bodies = []
    if offer_id:
        bodies.append({"cursor": "", "filter": {"offer_id": [offer_id], "visibility": "ALL"}, "limit": 100})
    if product_id:
        bodies.append({"cursor": "", "filter": {"product_id": [product_id], "visibility": "ALL"}, "limit": 100})
    bodies.append({"cursor": "", "filter": {"sku": [int(sku)], "visibility": "ALL"}, "limit": 100})
    if offer_id:
        bodies.append({"offer_id": [offer_id], "limit": 100})
    if product_id:
        bodies.append({"product_id": [product_id], "limit": 100})
    return bodies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--sku", required=True, help="ozon_sku (как показано на РНП Товары)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == args.store_id).first()
        if not creds:
            print("Для этого магазина не заданы ключи Ozon.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)

        product = db.query(Product).filter(Product.store_id == args.store_id, Product.ozon_sku == args.sku).first()
        offer_id = product.offer_id if product else None
        product_id = product.ozon_product_id if product else None
        print(f"Товар в базе: offer_id={offer_id!r}, ozon_product_id={product_id!r}")

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            data = None
            last_error: Exception | None = None
            for path in ("/v5/product/info/prices", "/v4/product/info/prices"):
                for body in _candidate_bodies(offer_id=offer_id, product_id=product_id, sku=args.sku):
                    try:
                        print(f"--- пробую {path} с телом: {json.dumps(body, ensure_ascii=False)}")
                        data = client._post(path, body)  # noqa: SLF001 — deliberate raw probe
                        print("    успех!")
                        break
                    except OzonAPIError as exc:
                        print(f"    ошибка: {exc}")
                        last_error = exc
                if data is not None:
                    break
            if data is None:
                print("\nНи одно из тестовых тел запроса не сработало ни для одного пути.")
                if last_error:
                    raise last_error
                return
    finally:
        db.close()

    print("\n=== ПОЛНЫЙ СЫРОЙ ОТВЕТ ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
