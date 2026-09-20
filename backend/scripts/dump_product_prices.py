"""Diagnostic: prints Product.price_rub/old_price_rub (as stored from
/v3/product/info/list's `price`/`old_price` fields — see OzonProductInfoItem
in app.services.ozon.schemas) for specific SKUs, read-only, no Ozon call.

Built 2026-09-20 to pin down the confirmed root cause of README's own
"Известная проблема" note on «СПП (расчёт)»: the column is computed from
order-line old_price/price (see order_daily_sync_service.aggregate_postings_
by_day), and the user's own real screenshot shows it running ~75% while her
Ozon cabinet's own СПП is ~40-53% for the same days. The user separately
uploaded a real "Цены и акции" export (prices.csv) with, per SKU, "Ваша
цена" (seller's own price), "Цена до скидки" (a higher reference/list
price), and "Цена для покупателя" (final price after ALL discounts
including Ozon's own SPP) — first-look arithmetic on that file suggests our
stored old_price matches "Цена до скидки" (the inflated reference), not
"Ваша цена" (the seller's real price), which would explain the overstated
percentage. This script's output is compared against that CSV's own values
for the SAME skus to confirm or reject that hypothesis on real data before
touching the formula.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/dump_product_prices.py \\
        --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \\
        --sku 5807024631 --sku 5716615794 --sku 5666596161
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.product import Product  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--sku", action="append", default=[], required=True)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        for sku in args.sku:
            product = db.query(Product).filter(Product.store_id == args.store_id, Product.ozon_sku == sku).first()
            if not product:
                print(f"SKU {sku}: не найден в нашей БД для этого магазина.")
                continue
            print(
                f"SKU {sku} ({product.name!r}): price_rub={product.price_rub}  "
                f"old_price_rub={product.old_price_rub}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
