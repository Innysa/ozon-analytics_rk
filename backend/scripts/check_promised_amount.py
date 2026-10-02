"""Diagnostic: reads promised_amount straight from the DB (already-synced
ProductWarehouseStock rows, no new Ozon call) for one offer_id — used
2026-10-02 to test the working hypothesis that promised_amount means
"товар едет на склад Ozon" against a real created-but-not-yet-shipped
supply order (status «Заполнение данных» in the user's cabinet, scheduled
to ship end of October). If promised_amount is already non-zero for these
SKUs NOW, before the supply order ships, that would mean the field
actually counts "создано, но не отгружено" supply orders rather than
"едет" — see ProductWarehouseStock's own docstring for the full story.

Usage (inside the running container):

    docker compose exec app python backend/scripts/check_promised_amount.py --offer-id этаж/кол/3/мет/сер/2
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.product_warehouse_stock import ProductWarehouseStock  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offer-id", required=True)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = db.query(ProductWarehouseStock).filter(ProductWarehouseStock.offer_id == args.offer_id).all()
        if not rows:
            print(f"Ничего не найдено для артикула {args.offer_id!r} — проверьте, что сохранилось именно это написание (попробуйте без решения регистра/пробелов), или что «Обновить остатки» уже запускали.")
            return
        print(f"SKU: {rows[0].ozon_sku}")
        total_promised = sum(r.promised_amount for r in rows)
        print(f"Сумма promised_amount по всем складам: {total_promised}\n")
        for r in rows:
            print(
                f"{r.warehouse_name:30} free_to_sell={r.free_to_sell_amount:5} "
                f"reserved={r.reserved_amount:5} promised={r.promised_amount:5}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
