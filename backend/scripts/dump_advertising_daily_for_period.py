"""Diagnostic: dumps the ALREADY-STORED AdvertisingDailyStatistic rows for
a store and date range, summed per day — no Ozon API call, just reads the
database, so it's safe to run anytime.

Built to compare the dashboard's "Расход на рекламу (авто, Performance
API)" (a straight SUM of spend_rub, see dashboard_service._sum_auto_ad_
spend) against a real account's own "Начисления" XLSX cabinet export,
"Оплата за клик" type, day by day — a real ~4% gap was found comparing
the two TOTALS for 01.09-19.09.2026 (498 741 ₽ in the XLSX for 18 closed
days vs 478 696 ₽ on the dashboard for 19 days), too small and too even
to be a single dropped day (unlike the earlier commission/revenue bug),
so this looks for whether the gap is spread evenly across every day or
concentrated in a few.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/dump_advertising_daily_for_period.py \\
        --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \\
        --date-from 2026-09-01 --date-to 2026-09-19
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.advertising_daily_statistic import AdvertisingDailyStatistic  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--date-from", required=True, help="ГГГГ-ММ-ДД")
    parser.add_argument("--date-to", required=True, help="ГГГГ-ММ-ДД")
    args = parser.parse_args()

    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to)

    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                AdvertisingDailyStatistic.date,
                func.count(AdvertisingDailyStatistic.id),
                func.coalesce(func.sum(AdvertisingDailyStatistic.spend_rub), 0),
                func.coalesce(func.sum(AdvertisingDailyStatistic.clicks), 0),
            )
            .where(
                AdvertisingDailyStatistic.store_id == args.store_id,
                AdvertisingDailyStatistic.date >= date_from,
                AdvertisingDailyStatistic.date <= date_to,
            )
            .group_by(AdvertisingDailyStatistic.date)
        ).all()
    finally:
        db.close()

    by_date = {d: (cnt, float(spend), int(clicks)) for d, cnt, spend, clicks in rows}
    all_days = []
    d = date_from
    while d <= date_to:
        all_days.append(d)
        d = date.fromordinal(d.toordinal() + 1)

    print(f"{'дата':12} {'строк':>7} {'spend_rub':>12} {'клики':>8}")
    total_spend = 0.0
    for d in all_days:
        entry = by_date.get(d)
        if entry is None:
            print(f"{d} {'НЕТ':>7} {'-':>12} {'-':>8}")
            continue
        cnt, spend, clicks = entry
        total_spend += spend
        print(f"{d} {cnt:>7} {spend:>12.2f} {clicks:>8}")

    print()
    print(f"ИТОГО за период: spend_rub={round(total_spend, 2)}")


if __name__ == "__main__":
    main()
