"""Diagnostic: dumps the ALREADY-STORED AccrualDailyStatistic rows for a
store and date range — no Ozon API call, just reads the database, so it's
safe to run anytime and costs nothing against Ozon's rate limit.

Built to find exactly WHICH day(s) diverge when a multi-day dashboard
total (commission_rub or delivered_sum_rub) doesn't match Ozon's own
cabinet total for the same range, even though individual days were
confirmed exact in isolation (see AccrualDailyStatistic's own docstring)
— a mismatch aggregated over many days could hide one bad day inside an
otherwise-correct sum.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/dump_accrual_daily_for_period.py \\
        --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \\
        --date-from 2026-09-01 --date-to 2026-09-19
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.accrual_daily_statistic import AccrualDailyStatistic  # noqa: E402


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
        rows = (
            db.query(AccrualDailyStatistic)
            .filter(
                AccrualDailyStatistic.store_id == args.store_id,
                AccrualDailyStatistic.date >= date_from,
                AccrualDailyStatistic.date <= date_to,
            )
            .order_by(AccrualDailyStatistic.date)
            .all()
        )
    finally:
        db.close()

    by_date = {r.date: r for r in rows}
    all_days = []
    d = date_from
    while d <= date_to:
        all_days.append(d)
        d = date.fromordinal(d.toordinal() + 1)

    print(f"{'дата':12} {'запись?':8} {'записей':>8} {'commission':>13} {'sales':>12} {'returns':>10} {'confirmed?':>11}")
    total_commission = 0.0
    total_sales = 0.0
    total_returns = 0.0
    for d in all_days:
        row = by_date.get(d)
        if row is None:
            print(f"{d} {'НЕТ':8} {'-':>8} {'-':>13} {'-':>12} {'-':>10} {'-':>11}")
            continue
        total_commission += float(row.commission_ozon_rub)
        total_sales += float(row.sales_rub)
        total_returns += float(row.returns_rub)
        print(
            f"{d} {'есть':8} {row.record_count:>8} {float(row.commission_ozon_rub):>13.2f} "
            f"{float(row.sales_rub):>12.2f} {float(row.returns_rub):>10.2f} {str(row.revenue_confirmed):>11}"
        )

    print()
    print(f"ИТОГО за период: commission={round(total_commission, 2)}  "
          f"sales+returns={round(total_sales + total_returns, 2)}  "
          f"(sales={round(total_sales, 2)}, returns={round(total_returns, 2)})")


if __name__ == "__main__":
    main()
