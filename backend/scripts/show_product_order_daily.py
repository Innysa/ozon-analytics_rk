"""Tiny diagnostic script: prints exactly what's stored right now in
ProductOrderDailyStatistic for one SKU — straight from this app's own
database, no Ozon API call, no browser involved. Added 2026-09-11 while
investigating a per-SKU order-count mismatch: several hypotheses (wrong
date field, timezone offset, FBO/FBS double-counting) were tested against
a real Ozon postings export and none reproduced the numbers the seller saw
on the «РНП Товары» page — the next thing to rule out is simple staleness
(the page/browser showing an older sync's data), which this script sidesteps
entirely by reading the database directly. Short output only.

Usage (inside the running container):

    docker compose exec app python backend/scripts/show_product_order_daily.py \\
        --store-id <id> --sku 3249061904

    # narrow the date range (default: last 20 days):
    docker compose exec app python backend/scripts/show_product_order_daily.py \\
        --store-id <id> --sku 3249061904 --date-from 2026-08-28 --date-to 2026-09-11
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.product_order_daily_statistic import ProductOrderDailyStatistic  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--sku", required=True)
    parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД, по умолчанию — 20 дней назад от --date-to")
    parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД, по умолчанию — сегодня")
    args = parser.parse_args()

    date_to = date.fromisoformat(args.date_to) if args.date_to else date.today()
    date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=20)

    db = SessionLocal()
    try:
        rows = (
            db.query(ProductOrderDailyStatistic)
            .filter(
                ProductOrderDailyStatistic.store_id == args.store_id,
                ProductOrderDailyStatistic.ozon_sku == args.sku,
                ProductOrderDailyStatistic.date >= date_from,
                ProductOrderDailyStatistic.date <= date_to,
            )
            .order_by(ProductOrderDailyStatistic.date, ProductOrderDailyStatistic.delivery_schema)
            .all()
        )
        if not rows:
            print("Нет ни одной строки в базе для этого SKU за указанный период.")
            return

        print(f"{'Дата':<12} {'Схема':<6} {'Заказано, шт':>13} {'Отменено, шт':>13}  Обновлено (updated_at)")
        totals_by_date: dict[str, int] = {}
        for row in rows:
            updated_at = row.updated_at.isoformat(timespec="seconds") if row.updated_at else "—"
            print(f"{row.date.isoformat():<12} {row.delivery_schema:<6} {row.ordered_units:>13} {row.cancelled_units:>13}  {updated_at}")
            totals_by_date.setdefault(row.date.isoformat(), 0)
            totals_by_date[row.date.isoformat()] += row.ordered_units

        print("\nИтого по дню (FBO+FBS сумма — то, что показывается на «РНП Товары»):")
        for d in sorted(totals_by_date):
            print(f"  {d}: {totals_by_date[d]}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
