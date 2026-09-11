"""Read-only diagnostic: for a given store/date range, prints the ad-spend
total from BOTH Ozon sources this app touches, side by side:

  1. AdvertisingDailyStatistic (Performance API's async statistics-report
     flow, per campaign/SKU/day) — what compute_dashboard()'s
     _sum_auto_ad_spend() sums for the Дашборд's "Расход на рекламу (авто,
     Performance API)" card.
  2. CashFlowStatementPeriod.services_items_json — Ozon's own FINANCE/
     billing cash-flow statement, which (per that model's own docstring)
     has been confirmed to carry a "MarketplaceServiceCostPerClick" line
     item under its `services` bucket. This is closer to what Ozon's own
     cabinet shows under "Продвижение и реклама" in the "Начисления"
     breakdown.

Why this script exists: a real account's Дашборд showed "Расход на
рекламу (авто, Performance API)" = 253 863.46 ₽ for 2026-09-01..09-11,
while the SAME account's own Ozon cabinet "Начисления" screen showed
"Продвижение и реклама" = 331 703 ₽ for the same period — a real ~78 000 ₽
(~23%) gap between two Ozon-reported numbers for (nominally) the same
thing. Unlike the earlier "+1070%" bug (a real data GAP in
AdvertisingDailyStatistic — some days had zero rows, confirmed via the
РНП page's own daily breakdown), this gap is different in kind: both
totals have real, non-zero data for the same days, so this isn't (yet)
confirmed as a bug in our code at all — it may be a genuine difference in
what each Ozon API counts (statistics-report is per click/impression
DATE; the finance cash-flow statement bills by Ozon's OWN weekly
settlement periods, which don't line up 1:1 with calendar days — see
CashFlowStatementPeriod's own docstring). This script prints both raw
numbers plus every individual MarketplaceServiceCostPerClick-like line
item, so the actual cause (missing campaigns? a genuine billing-date vs
click-date offset? a different service scope entirely?) can be confirmed
from real data instead of guessed at.

No Ozon API call — reads only what previous syncs already saved (both the
AdvertisingDailyStatistic auto-sync and the cash-flow-statement auto-sync
must have already run for the store/period in question), so it's free to
re-run and costs nothing against any Ozon rate limit.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/compare_advertising_spend_sources.py \\
        --store-id <id> --date-from 2026-09-01 --date-to 2026-09-11
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.advertising_daily_statistic import AdvertisingDailyStatistic  # noqa: E402
from app.models.cash_flow_statement_period import CashFlowStatementPeriod  # noqa: E402

# Substrings a services_items_json entry's `name` is checked against to be
# counted as "advertising" here — only MarketplaceServiceCostPerClick has
# been directly confirmed (see CashFlowStatementPeriod's own docstring);
# the others are speculative alternate spellings kept for visibility, NOT
# assumed correct — everything under "all services items" below is printed
# regardless, precisely so an unrecognized name doesn't get silently missed.
AD_NAME_HINTS = ("CostPerClick", "Advert", "Promo", "Boost")


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
        print("=" * 70)
        print(f"1) AdvertisingDailyStatistic (Performance API) — {date_from} — {date_to}")
        print("=" * 70)
        auto_total = db.scalar(
            select(func.coalesce(func.sum(AdvertisingDailyStatistic.spend_rub), 0)).where(
                AdvertisingDailyStatistic.store_id == args.store_id,
                AdvertisingDailyStatistic.date >= date_from,
                AdvertisingDailyStatistic.date <= date_to,
            )
        )
        print(f"    ИТОГО spend_rub (как на Дашборде): {auto_total}")
        by_day = db.execute(
            select(AdvertisingDailyStatistic.date, func.coalesce(func.sum(AdvertisingDailyStatistic.spend_rub), 0))
            .where(
                AdvertisingDailyStatistic.store_id == args.store_id,
                AdvertisingDailyStatistic.date >= date_from,
                AdvertisingDailyStatistic.date <= date_to,
            )
            .group_by(AdvertisingDailyStatistic.date)
            .order_by(AdvertisingDailyStatistic.date)
        ).all()
        for d, total in by_day:
            print(f"    {d}: {total}")
        if not by_day:
            print("    (нет ни одной строки за этот период — проверьте, что автосбор статистики рекламы запускался)")

        print()
        print("=" * 70)
        print(f"2) CashFlowStatementPeriod.services_items_json — периоды, ЦЕЛИКОМ входящие в {date_from} — {date_to}")
        print("=" * 70)
        periods = list(
            db.scalars(
                select(CashFlowStatementPeriod).where(
                    CashFlowStatementPeriod.store_id == args.store_id,
                    CashFlowStatementPeriod.period_begin >= date_from,
                    CashFlowStatementPeriod.period_end <= date_to,
                ).order_by(CashFlowStatementPeriod.period_begin)
            )
        )
        if not periods:
            print("    (нет периодов Ozon, целиком входящих в этот диапазон — "
                  "возможно, диапазон уже, чем шаг недельных периодов Ozon, "
                  "или cash-flow ещё не синхронизировался за эти даты)")
        ad_total = 0.0
        all_names: dict[str, float] = {}
        for p in periods:
            print(f"    Период {p.period_begin} — {p.period_end} (services_total={p.services_total}):")
            if not p.services_items_json:
                print("        (services_items_json пуст)")
                continue
            try:
                items = json.loads(p.services_items_json)
            except (TypeError, ValueError):
                print("        (не удалось распарсить services_items_json)")
                continue
            for item in items:
                name = item.get("name") or "?"
                price = float(item.get("price") or 0)
                all_names[name] = all_names.get(name, 0.0) + price
                flag = " <-- похоже на рекламу" if any(h.lower() in name.lower() for h in AD_NAME_HINTS) else ""
                print(f"        {name}: {price}{flag}")
                if flag:
                    ad_total += price

        print()
        print(f"    ИТОГО по статьям, похожим на рекламу ({'/'.join(AD_NAME_HINTS)}): {round(ad_total, 2)}")

        print()
        print("=" * 70)
        print("СРАВНЕНИЕ:")
        print(f"    Performance API (Дашборд): {auto_total}")
        print(f"    Cash-flow (похоже на рекламу): {round(ad_total, 2)}")
        if auto_total and ad_total:
            print(f"    Разница: {round(float(ad_total) - float(auto_total), 2)} "
                  f"({round((float(ad_total) - float(auto_total)) / float(auto_total) * 100, 2)}%)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
