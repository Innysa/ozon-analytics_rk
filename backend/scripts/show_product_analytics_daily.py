"""Tiny diagnostic script: prints exactly what's stored right now in
ProductAnalyticsDailyStatistic (Ozon Analytics API funnel) for one SKU,
side by side with ProductOrderDailyStatistic (postings) for the same days,
AND which one «РНП Товары»'s «Заказано, шт» actually shows for that day
(see app.services.product_planner_service._aggregate_month: funnel wins
whenever a funnel row exists for that (sku, day), postings is the fallback
otherwise). Added 2026-09-24 while investigating a real per-SKU mismatch
the seller found between the app and her own Ozon "Заказы" CSV export,
where several days simply hadn't changed after the funnel-preference
switch shipped — the fastest way to tell "funnel sync hasn't covered this
day yet" apart from "funnel covered it but disagrees with postings" is to
look at what's actually in the database, not guess from a screenshot.

Usage (inside the running container):

    docker compose exec app python backend/scripts/show_product_analytics_daily.py \\
        --store-id <id> --sku 3195982153

    # or by name instead of the UUID:
    docker compose exec app python backend/scripts/show_product_analytics_daily.py \\
        --store-name "Комфорт дом" --sku 3195982153

    # narrow the date range (default: last 30 days):
    docker compose exec app python backend/scripts/show_product_analytics_daily.py \\
        --store-name "Комфорт дом" --sku 3195982153 --date-from 2026-09-01 --date-to 2026-09-24
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.product_analytics_daily_statistic import ProductAnalyticsDailyStatistic  # noqa: E402
from app.models.product_order_daily_statistic import ProductOrderDailyStatistic  # noqa: E402
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
    parser.add_argument("--store-id", default=None, help="внутренний UUID магазина")
    parser.add_argument("--store-name", default=None, help="имя магазина — альтернатива --store-id")
    parser.add_argument("--sku", required=True)
    parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД, по умолчанию — 30 дней назад от --date-to")
    parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД, по умолчанию — сегодня")
    args = parser.parse_args()

    if not args.store_id and not args.store_name:
        print("Укажите --store-id или --store-name.")
        return

    date_to = date.fromisoformat(args.date_to) if args.date_to else date.today()
    date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=30)

    db = SessionLocal()
    try:
        store_id = _resolve_store_id(db, store_id=args.store_id, store_name=args.store_name)
        if not store_id:
            return

        funnel_rows = (
            db.query(ProductAnalyticsDailyStatistic)
            .filter(
                ProductAnalyticsDailyStatistic.store_id == store_id,
                ProductAnalyticsDailyStatistic.ozon_sku == args.sku,
                ProductAnalyticsDailyStatistic.date >= date_from,
                ProductAnalyticsDailyStatistic.date <= date_to,
            )
            .all()
        )
        funnel_by_date = {r.date.isoformat(): r for r in funnel_rows}

        postings_rows = (
            db.query(ProductOrderDailyStatistic)
            .filter(
                ProductOrderDailyStatistic.store_id == store_id,
                ProductOrderDailyStatistic.ozon_sku == args.sku,
                ProductOrderDailyStatistic.date >= date_from,
                ProductOrderDailyStatistic.date <= date_to,
            )
            .all()
        )
        postings_units_by_date: dict[str, int] = {}
        for r in postings_rows:
            key = r.date.isoformat()
            postings_units_by_date[key] = postings_units_by_date.get(key, 0) + r.ordered_units

        all_dates = sorted(set(funnel_by_date) | set(postings_units_by_date))
        if not all_dates:
            print("Нет ни одной строки в базе (ни воронки, ни отгрузок) для этого SKU за указанный период.")
            return

        print(f"{'Дата':<12} {'Воронка есть?':<14} {'Воронка, шт':>12} {'Отгрузки, шт':>13} {'РНП Товары покажет':>19}  Обновлено воронки")
        for d in all_dates:
            funnel = funnel_by_date.get(d)
            postings_units = postings_units_by_date.get(d)
            funnel_units = funnel.ordered_units if funnel else None
            effective = funnel_units if funnel is not None else postings_units
            updated_at = funnel.updated_at.isoformat(timespec="seconds") if funnel and funnel.updated_at else "—"
            print(
                f"{d:<12} {'да' if funnel else 'нет':<14} "
                f"{funnel_units if funnel_units is not None else '—':>12} "
                f"{postings_units if postings_units is not None else '—':>13} "
                f"{effective if effective is not None else '—':>19}  {updated_at}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
