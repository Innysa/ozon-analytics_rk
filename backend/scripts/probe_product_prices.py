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

Usage (on the real server, against the real database) — --store-id is now
OPTIONAL: with just --sku, resolved automatically the same way as
show_spp_day_breakdown.py (по SKU, or the only store in the database) —
--store-name never worked reliably for this user's terminal (Cyrillic
doesn't type OR paste there, confirmed live 2026-09-24):

    docker compose exec app python backend/scripts/probe_product_prices.py \\
        --sku 4052748220

**ДОБАВЛЕНО 2026-09-24 (второй раунд)**: пользователь показала товар, у
которого marketing_seller_price ОКАЗАЛСЯ РАВЕН price (оба 3500 — «Предельная
цена без акций», см. её скриншот кабинета), при этом реальная цена на
витрине (1662-2081 ₽ в разных сценариях) СИЛЬНО ниже — то есть
marketing_seller_price НЕ объясняет весь разрыв для этого товара. Найдено
через WebSearch (pkg.go.dev/github.com/andmetoo/ozon-api-client — Go SDK,
структура PricesResponseItemPrice): в сыром ответе есть ЕЩЁ одно поле,
`marketing_price`, которое этот проект раньше не сохранял вообще (не было
даже в OzonProductPriceDetail). Официально помечено Ozon как deprecated
(«price.marketing_price в документации больше не описан») именно потому,
что «не учитывает все скидки, рекламу и персональные предложения» — то
есть даже это поле не гарантирует точного совпадения с ценой конкретного
покупателя, но может быть ближе к «Цена по FBO»/«Цена с картой», чем
marketing_seller_price. Этот повторный прогон печатает ПОЛНЫЙ сырой JSON
именно чтобы увидеть значение marketing_price (наш Pydantic-класс его
игнорирует — extra="allow" держит в сырых данных, но не в model-полях) и
сверить с реальными цифрами из кабинета.
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
