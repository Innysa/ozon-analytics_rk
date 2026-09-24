"""Tiny diagnostic script: prints exactly what's stored right now in
Product (price_rub/old_price_rub/marketing_seller_price_rub) and today's
ProductPriceDailySnapshot for one SKU — straight from this app's own
database, no Ozon API call. Added 2026-09-24 while investigating why
«СПП (расчёт)» didn't change at all after the user ran both «Синхронизировать
с Ozon» (catalog) and «Обновить заказы (авто)» (orders) — the fastest way
to tell "the new marketing_seller_price_rub column is still NULL" (catalog
sync didn't actually populate it) apart from "it IS populated but orders
sync isn't reading it" is to look at the database directly.

Usage (inside the running container):

    docker compose exec app python backend/scripts/show_product_price_fields.py \\
        --store-id <id> --sku 3195982153

    # or by name instead of the UUID:
    docker compose exec app python backend/scripts/show_product_price_fields.py \\
        --store-name "Комфорт дом" --sku 3195982153
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.product_price_daily_snapshot import ProductPriceDailySnapshot  # noqa: E402
from app.models.store import Store  # noqa: E402


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
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--store-name", default=None)
    parser.add_argument("--sku", required=True)
    args = parser.parse_args()

    if not args.store_id and not args.store_name:
        print("Укажите --store-id или --store-name.")
        return

    db = SessionLocal()
    try:
        store_id = _resolve_store_id(db, store_id=args.store_id, store_name=args.store_name)
        if not store_id:
            return

        product = db.query(Product).filter(Product.store_id == store_id, Product.ozon_sku == args.sku).first()
        if not product:
            print("Товар с таким SKU не найден в базе для этого магазина.")
            return

        print("=== Product (текущий снимок) ===")
        print(f"price_rub:                   {product.price_rub}")
        print(f"old_price_rub:                {product.old_price_rub}")
        print(f"marketing_seller_price_rub:   {product.marketing_seller_price_rub}")
        print(f"updated_at:                   {product.updated_at}")

        snapshots = (
            db.query(ProductPriceDailySnapshot)
            .filter(ProductPriceDailySnapshot.store_id == store_id, ProductPriceDailySnapshot.ozon_sku == args.sku)
            .order_by(ProductPriceDailySnapshot.date.desc())
            .limit(5)
            .all()
        )
        print("\n=== ProductPriceDailySnapshot (последние 5 дней) ===")
        if not snapshots:
            print("Нет ни одной строки.")
        for s in snapshots:
            print(
                f"{s.date.isoformat()}  price_rub={s.price_rub}  "
                f"marketing_seller_price_rub={s.marketing_seller_price_rub}  updated_at={s.updated_at}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
