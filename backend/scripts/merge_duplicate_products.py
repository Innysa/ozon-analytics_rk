"""One-off maintenance script: merges duplicate Product rows within a store
that represent the same real-world Ozon product tracked under two different
rows — the historic sku=0 duplicate-row bug (see
app.services.product_merge's module docstring for how this arises; confirmed
live for offer_id "мус/вед/бел1/3", real sku 5716615794). This is exactly
the situation that made /sync/ozon-products and the "Позиции в поиске"
auto-sync fail with:

    psycopg.errors.UniqueViolation: duplicate key value violates unique
    constraint "uq_product_store_sku"

Both sync paths now detect and merge this automatically going forward (see
app.services.product_merge), so a fresh sync run would eventually self-heal
this on its own — but this script fixes any existing duplicates immediately,
without waiting on that, and also catches any other dormant duplicate in the
catalog that hasn't been hit by a sync yet.

Safe to run repeatedly: once no duplicates remain, running it again is a
no-op. ALWAYS run without --apply first (the default) and read the printed
plan before re-running with --apply — merging reassigns every
review/statistic/recommendation/history row from the losing duplicate onto
the surviving product, then deletes the losing row. This cannot be undone
except by restoring the database from a backup, so review the plan first.

Usage (run from the backend/ directory, with the same environment the app
itself uses so DATABASE_URL resolves to the right database):

    python scripts/merge_duplicate_products.py                     # dry run, all stores
    python scripts/merge_duplicate_products.py --store-id <id>     # dry run, one store
    python scripts/merge_duplicate_products.py --apply             # actually merge and commit

    # Targeted lookup/merge for one already-known product (e.g. from a
    # UniqueViolation error message naming a specific sku): finds every row
    # in the store matching EITHER value, so a placeholder that doesn't (yet)
    # share the real sku is still found via its offer_id, and vice versa.
    python scripts/merge_duplicate_products.py --store-id <id> --sku 5716615794 --offer-id "мус/вед/бел1/3"
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.services.product_merge import _related_row_count, merge_duplicate_products, pick_survivor  # noqa: E402


def _find_duplicate_groups(db, store_id: str | None) -> list[list[Product]]:
    """Groups products that share the same (store_id, offer_id) — the key
    the sku=0 retry fallback uses — or, for the rare row with no offer_id at
    all, the same (store_id, ozon_product_id). A store with clean data
    produces zero groups of size > 1."""
    query = db.query(Product)
    if store_id:
        query = query.filter(Product.store_id == store_id)
    products = query.order_by(Product.store_id, Product.created_at).all()

    by_offer: dict[tuple[str, str], list[Product]] = defaultdict(list)
    by_product_id: dict[tuple[str, int], list[Product]] = defaultdict(list)
    for p in products:
        if p.offer_id:
            by_offer[(p.store_id, p.offer_id)].append(p)
        elif p.ozon_product_id is not None:
            by_product_id[(p.store_id, p.ozon_product_id)].append(p)

    return [g for g in by_offer.values() if len(g) > 1] + [g for g in by_product_id.values() if len(g) > 1]


def _find_targeted_groups(db, store_id: str | None, sku: str | None, offer_id: str | None) -> list[list[Product]]:
    """Every product matching EITHER the given sku OR offer_id, grouped by
    store — for investigating/fixing one specific already-known product
    (e.g. named in a UniqueViolation error), rather than scanning the whole
    catalog. Deliberately OR, not AND: a placeholder row usually shares the
    offer_id but not yet the sku, so matching on sku alone would miss it."""
    conditions = []
    if sku:
        conditions.append(Product.ozon_sku == sku)
    if offer_id:
        conditions.append(Product.offer_id == offer_id)
    query = db.query(Product).filter(or_(*conditions))
    if store_id:
        query = query.filter(Product.store_id == store_id)
    products = query.order_by(Product.store_id, Product.created_at).all()

    by_store: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        by_store[p.store_id].append(p)
    return list(by_store.values())


def _merge_group(db, group: list[Product], *, apply: bool) -> None:
    survivor = group[0]
    for other in group[1:]:
        keep, remove = pick_survivor(db, survivor, other)
        print(
            f"  {'MERGE' if apply else 'WOULD MERGE'}: keep {keep.id} "
            f"(sku={keep.ozon_sku!r}, offer_id={keep.offer_id!r}, name={keep.name!r}) "
            f"<- remove {remove.id} (sku={remove.ozon_sku!r}, offer_id={remove.offer_id!r}, "
            f"related_rows={_related_row_count(db, remove.id)})"
        )
        if apply:
            merge_duplicate_products(db, keep=keep, remove=remove)
        survivor = keep


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None, help="Ограничиться одним магазином (по умолчанию — все)")
    parser.add_argument("--sku", default=None, help="Найти/слить конкретный ozon_sku (можно вместе с --offer-id)")
    parser.add_argument("--offer-id", default=None, help="Найти/слить конкретный offer_id (можно вместе с --sku)")
    parser.add_argument("--apply", action="store_true", help="Реально слить и закоммитить (по умолчанию — только показать план)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.sku or args.offer_id:
            groups = _find_targeted_groups(db, args.store_id, args.sku, args.offer_id)
            if not groups:
                print("Ничего не найдено по заданным sku/offer_id.")
                return
            for group in groups:
                store_id = group[0].store_id
                print(f"\nМагазин {store_id}: найдено {len(group)} строк(и) по sku={args.sku!r} / offer_id={args.offer_id!r}")
                for p in group:
                    print(f"  {p.id}: sku={p.ozon_sku!r}, offer_id={p.offer_id!r}, product_id={p.ozon_product_id!r}, name={p.name!r}")
                if len(group) > 1:
                    _merge_group(db, group, apply=args.apply)
                else:
                    print("  (это не дубликат — найдена только одна строка)")
        else:
            groups = _find_duplicate_groups(db, args.store_id)
            if not groups:
                print("Дубликатов не найдено.")
                return

            print(f"Найдено групп дубликатов: {len(groups)}")
            for group in groups:
                store_id = group[0].store_id
                key = group[0].offer_id or group[0].ozon_product_id
                print(f"\nМагазин {store_id}, offer_id/product_id={key!r}: {len(group)} строк(и)")
                _merge_group(db, group, apply=args.apply)

        if args.apply:
            db.commit()
            print("\nГотово — изменения сохранены.")
        else:
            db.rollback()
            print("\nЭто был dry-run — ничего не изменено. Запустите с --apply, чтобы применить.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
