"""Diagnostic: raw probe of candidate Ozon Seller API stock methods —
UNVERIFIED contracts, never called before in this project (catalogued in
docs/ozon-seller-api-methods.md's «Аналитика»/«Товары» sections but
unused). Written 2026-10-02 after the user asked for a new «Остатки» tab
showing: сколько на FBO, сколько на FBS, сколько едет в пути на склад
Ozon, и сколько сейчас в поставке, но ещё не отгружено — the existing
Product.fbo_stock/fbs_stock (from /v3/product/info/list) only cover the
first two of those four states, so this probes the other candidates:

  - POST /v2/analytics/stock_on_warehouses — per-warehouse breakdown,
    flagged in docs/ozon-seller-api-methods.md as "возможный кандидат на
    более точные «Остатки товаров» (по складам" — contract never checked.
  - POST /v1/analytics/stocks — newer stock-health analytics method, not
    used anywhere in this project yet.
  - POST /v1/product/info/stocks-by-warehouse/fbo — per-warehouse FBO
    detail by SKU.
  - POST /v3/supply-order/list — poставки (supply orders): the most
    likely source of "в поставке, но не отгружено" (a created/draft supply
    order whose cargo hasn't shipped yet) and possibly "в пути" (a shipped
    but not-yet-arrived supply order) as two different order STATUSES on
    the same object, rather than two different stock-count fields.

Tries a few candidate request bodies per endpoint (field naming isn't
confirmed for any of these) and prints the FULL raw response for whichever
succeeds, so the real field names can be read off directly rather than
guessed — same discipline as probe_product_prices.py.

Usage (on the real server, against the real database) — --store-id is
OPTIONAL, resolved automatically same as the other probe scripts:

    docker compose exec app python backend/scripts/probe_stocks.py --sku 4052748220
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
from app.models.store import Store  # noqa: E402
from app.services.ozon.client import OzonAPIError, OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402


def _resolve_store_id(db, *, store_id: str | None, sku: str) -> str | None:
    if store_id:
        return store_id
    by_sku = db.query(Product).filter(Product.ozon_sku == sku).all()
    distinct_stores = {p.store_id for p in by_sku}
    if len(distinct_stores) == 1:
        store = db.get(Store, next(iter(distinct_stores)))
        print(f"Найден магазин по SKU: {store.id} — {store.name}")
        return store.id
    all_stores = db.query(Store).all()
    if len(all_stores) == 1:
        print(f"В базе всего один магазин: {all_stores[0].id} — {all_stores[0].name}")
        return all_stores[0].id
    print("Не удалось определить магазин автоматически. Доступные магазины (скопируйте id для --store-id):")
    for s in all_stores:
        print(f"    {s.id}  —  {s.name}")
    return None


def _try_endpoint(client: OzonSellerClient, *, path: str, bodies: list[dict]) -> dict | None:
    for body in bodies:
        try:
            print(f"--- пробую {path} с телом: {json.dumps(body, ensure_ascii=False)}")
            data = client._post(path, body)  # noqa: SLF001 — deliberate raw probe
            print("    успех!")
            return data
        except OzonAPIError as exc:
            print(f"    ошибка: {exc}")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--sku", required=True, help="ozon_sku (как показано на РНП Товары)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        store_id = _resolve_store_id(db, store_id=args.store_id, sku=args.sku)
        if not store_id:
            return

        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == store_id).first()
        if not creds:
            print("Для этого магазина не заданы ключи Ozon.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)

        product = db.query(Product).filter(Product.store_id == store_id, Product.ozon_sku == args.sku).first()
        offer_id = product.offer_id if product else None
        print(f"Товар в базе: offer_id={offer_id!r}, ozon_sku={args.sku!r}")
        print(f"Текущие Product.fbo_stock={product.fbo_stock if product else '?'}  fbs_stock={product.fbs_stock if product else '?'}\n")

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            print("\n========== /v2/analytics/stock_on_warehouses ==========")
            data = _try_endpoint(
                client,
                path="/v2/analytics/stock_on_warehouses",
                bodies=[
                    {"limit": 100, "offset": 0, "warehouse_type": "ALL"},
                    {"limit": 100, "offset": 0},
                    {"limit": 100, "offset": 0, "skus": [int(args.sku)]},
                ],
            )
            if data:
                print(json.dumps(data, ensure_ascii=False, indent=2)[:6000])

            print("\n========== /v1/analytics/stocks ==========")
            data = _try_endpoint(
                client,
                path="/v1/analytics/stocks",
                bodies=[
                    {"skus": [args.sku]},
                    {"sku": [int(args.sku)]},
                ],
            )
            if data:
                print(json.dumps(data, ensure_ascii=False, indent=2)[:6000])

            print("\n========== /v1/product/info/stocks-by-warehouse/fbo ==========")
            data = _try_endpoint(
                client,
                path="/v1/product/info/stocks-by-warehouse/fbo",
                bodies=[
                    {"sku": [int(args.sku)], "limit": 100},
                ],
            )
            if data:
                print(json.dumps(data, ensure_ascii=False, indent=2)[:6000])

            print("\n========== /v3/supply-order/list ==========")
            data = _try_endpoint(
                client,
                path="/v3/supply-order/list",
                bodies=[
                    {"filter": {}, "limit": 20},
                    {"limit": 20},
                ],
            )
            if data:
                print(json.dumps(data, ensure_ascii=False, indent=2)[:6000])
    finally:
        db.close()


if __name__ == "__main__":
    main()
