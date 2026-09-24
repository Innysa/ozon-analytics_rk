"""Diagnostic: for one SKU, prints per-day the raw numbers behind «СПП
(расчёт)» on РНП Товары — ProductOrderDailyStatistic.ordered_sum_seller_
price_rub / ordered_sum_discounted_for_known_seller_price_rub (written
ONCE at sync time, from whatever seller-price map was available THEN —
they are NOT recomputed automatically when Product/ProductPriceDailySnapshot
later gets a better marketing_seller_price_rub value) next to that same
day's ProductPriceDailySnapshot row (price_rub / marketing_seller_price_rub)
— added 2026-09-24 while investigating the user's report that «РНП»
(store total) now shows plausible СПП since yesterday but «РНП Товары»
(per-product) still doesn't, for individual days.

Usage (inside the running container) — --store-id/--store-name are now
OPTIONAL: with just --sku, the script finds the store on its own (by SKU,
or the only store in the database):

    docker compose exec app python backend/scripts/show_spp_day_breakdown.py \\
        --sku 3249061904 --date-from 2026-09-11 --date-to 2026-09-24
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.product_order_daily_statistic import ProductOrderDailyStatistic  # noqa: E402
from app.models.product_price_daily_snapshot import ProductPriceDailySnapshot  # noqa: E402
from app.models.store import Store  # noqa: E402


def _resolve_store_id(db, *, store_id: str | None, store_name: str | None, sku: str) -> str | None:
    """Кириллица не вводится в терминале пользователя ни вставкой, ни
    печатью (подтверждено вживую 2026-09-24 — --store-name с кириллицей
    пришёл ПУСТЫМ, argparse отказал), поэтому --store-name дальше не
    единственный путь: если он не задан, сначала пробуем найти магазин
    ПО САМОМУ SKU (обычно уникален), затем — если в базе всего один
    магазин, берём его; и только если ничего не помогло, печатаем список
    магазинов с их id (ASCII UUID — его вставить в терминал можно), чтобы
    в следующий раз использовать --store-id."""
    if store_id:
        return store_id
    if store_name:
        matches = db.query(Store).filter(Store.name.ilike(f"%{store_name}%")).all()
        if len(matches) == 1:
            print(f"Найден магазин по имени: {matches[0].id} — {matches[0].name}")
            return matches[0].id
        if matches:
            print(f"Найдено несколько магазинов, подходящих под «{store_name}» — уточните --store-id:")
            for m in matches:
                print(f"    {m.id}  —  {m.name}")
            return None

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--store-name", default=None)
    parser.add_argument("--sku", required=True)
    parser.add_argument("--date-from", required=True, help="YYYY-MM-DD")
    parser.add_argument("--date-to", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to)

    db = SessionLocal()
    try:
        store_id = _resolve_store_id(db, store_id=args.store_id, store_name=args.store_name, sku=args.sku)
        if not store_id:
            return

        product = db.query(Product).filter(Product.store_id == store_id, Product.ozon_sku == args.sku).first()
        if product:
            print(f"\nProduct (текущий снимок): price_rub={product.price_rub}  marketing_seller_price_rub={product.marketing_seller_price_rub}\n")
        else:
            print("\nВНИМАНИЕ: товар с таким SKU не найден в Product для этого магазина.\n")

        orders = {
            r.date: r
            for r in db.query(ProductOrderDailyStatistic).filter(
                ProductOrderDailyStatistic.store_id == store_id,
                ProductOrderDailyStatistic.ozon_sku == args.sku,
                ProductOrderDailyStatistic.date >= date_from,
                ProductOrderDailyStatistic.date <= date_to,
            ).all()
        }
        snapshots = {
            s.date: s
            for s in db.query(ProductPriceDailySnapshot).filter(
                ProductPriceDailySnapshot.store_id == store_id,
                ProductPriceDailySnapshot.ozon_sku == args.sku,
                ProductPriceDailySnapshot.date >= date_from,
                ProductPriceDailySnapshot.date <= date_to,
            ).all()
        }

        header = (
            f"{'Дата':12} {'снимок?':8} {'снимок.price':13} {'снимок.msp':11} | "
            f"{'заказ.шт':9} {'база(₽)':10} {'оплач(₽)':10} {'шт.известн':11} {'СПП%':7}"
        )
        print(header)
        print("-" * len(header))

        d = date_from
        while d <= date_to:
            snap = snapshots.get(d)
            snap_flag = "есть" if snap else "нет"
            snap_price = f"{snap.price_rub:.2f}" if snap else "—"
            snap_msp = f"{snap.marketing_seller_price_rub:.2f}" if snap and snap.marketing_seller_price_rub is not None else "—"

            row = orders.get(d)
            if row:
                base = float(row.ordered_sum_seller_price_rub or 0)
                paid = float(row.ordered_sum_discounted_for_known_seller_price_rub or 0)
                known_units = row.ordered_units_with_known_seller_price
                spp = f"{(base - paid) / base * 100:.2f}%" if base > 0 else "—"
                print(
                    f"{d.isoformat():12} {snap_flag:8} {snap_price:>13} {snap_msp:>11} | "
                    f"{row.ordered_units:9} {base:10.2f} {paid:10.2f} {known_units:11} {spp:7}"
                )
            else:
                print(f"{d.isoformat():12} {snap_flag:8} {snap_price:>13} {snap_msp:>11} | (нет строки ProductOrderDailyStatistic)")
            d += timedelta(days=1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
