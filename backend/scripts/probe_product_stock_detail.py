"""Diagnostic: dumps the FULL raw `stocks` structure Ozon returns for one
product (POST /v3/product/info/list) — including any fields our own
OzonProductStockItem schema doesn't explicitly map (it uses `extra="allow"`,
so they're captured, just not normally printed).

Built to find what actually distinguishes a "virtual warehouse" stock entry
from a real FBS warehouse entry — the user confirmed (2026-09-19) that a
fixed 22 units on FBS is a virtual-warehouse upload baked into the same
`fbs_stock` total our sync computes by blindly summing every stock entry
whose source=="fbs" (see app.api.routes.sync.sync_ozon_products's
_upsert), with no distinction for warehouse identity. Before changing that
sum, this dumps the raw per-entry data (source, sku, present, reserved,
and anything else Ozon actually sends) to find the real field to key off.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/probe_product_stock_detail.py \\
        --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \\
        --offer-id "апт/конт/24л"
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
from app.services.ozon.client import OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--offer-id", action="append", default=[], help="можно указать несколько раз")
    parser.add_argument("--sku", action="append", default=[], help="ozon_sku — offer_id найдётся сам в нашей БД")
    args = parser.parse_args()
    if not args.offer_id and not args.sku:
        print("Укажите --offer-id или --sku (можно несколько раз).")
        return

    db = SessionLocal()
    try:
        offer_ids = list(args.offer_id)
        for sku in args.sku:
            product = db.query(Product).filter(Product.store_id == args.store_id, Product.ozon_sku == sku).first()
            if not product:
                print(f"SKU {sku}: не найден в нашей БД для этого магазина.")
                continue
            offer_ids.append(product.offer_id)
        if not offer_ids:
            print("Ни один offer_id не найден.")
            return

        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == args.store_id).first()
        if not creds:
            print("Для этого магазина не заданы ключи Ozon.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            info = client.get_products_info_by_offer_id(offer_ids)
    finally:
        db.close()

    for item in info.items:
        print("=" * 70)
        print(f"offer_id={item.offer_id!r}  sku={item.sku}  name={item.name!r}")
        if not item.stocks:
            print("    stocks: пусто")
            continue
        print(f"    stocks.has_stock={item.stocks.has_stock}")
        for i, stock in enumerate(item.stocks.stocks):
            print(f"    stocks.stocks[{i}] (все поля, включая незамапленные): {json.dumps(stock.model_dump(), ensure_ascii=False)}")


if __name__ == "__main__":
    main()
