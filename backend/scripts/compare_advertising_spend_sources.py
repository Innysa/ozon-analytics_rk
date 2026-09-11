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
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.advertising_daily_statistic import AdvertisingDailyStatistic  # noqa: E402
from app.models.cash_flow_statement_period import CashFlowStatementPeriod  # noqa: E402
from app.models.store import Store  # noqa: E402

# Substrings a services_items_json entry's `name` is checked against to be
# counted as "advertising" here — only MarketplaceServiceCostPerClick has
# been directly confirmed (see CashFlowStatementPeriod's own docstring);
# the others are speculative alternate spellings kept for visibility, NOT
# assumed correct — everything under "all services items" below is printed
# regardless, precisely so an unrecognized name doesn't get silently missed.
AD_NAME_HINTS = ("CostPerClick", "Advert", "Promo", "Boost")


def _resolve_store_id(db, *, store_id: str | None, store_name: str | None) -> str | None:
    """--store-id is the internal UUID (Store.id) — NOT Ozon's own numeric
    Client-Id, which looks similar (a plain number) but matches nothing
    here and silently returns zero rows rather than an error. --store-name
    sidesteps needing that UUID at all: a case-insensitive substring match
    against Store.name, run right here (no separate lookup command/script,
    no shell-quoting to get right)."""
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
    parser.add_argument("--store-name", default=None, help="имя магазина (например, 'Комфорт дом') — альтернатива --store-id")
    parser.add_argument("--date-from", required=True, help="ГГГГ-ММ-ДД")
    parser.add_argument("--date-to", required=True, help="ГГГГ-ММ-ДД")
    args = parser.parse_args()
    if not args.store_id and not args.store_name:
        print("Укажите --store-id или --store-name.")
        return
    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to)

    db = SessionLocal()
    try:
        store_id = _resolve_store_id(db, store_id=args.store_id, store_name=args.store_name)
        if not store_id:
            return
        args.store_id = store_id

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
        print(f"2) CashFlowStatementPeriod.services_items_json — периоды, ПЕРЕСЕКАЮЩИЕСЯ с {date_from} — {date_to}")
        print("=" * 70)
        # OVERLAP, not just full containment — a period that only partially
        # overlaps still gets PRINTED (with a clear "ИСКЛЮЧЁН" label) instead
        # of silently vanishing. Silently summing only fully-contained
        # periods produced a real false conclusion here once already: an
        # 11-day range (2026-09-01..09-11) had only ONE 6-day Ozon period
        # (09-01..09-06) fully inside it, so a "253 863 vs 195 322" compare
        # was actually "11 days of Performance API vs 6 days of cash-flow" —
        # nowhere near apples-to-apples, independent of any real gap between
        # the two sources.
        periods = list(
            db.scalars(
                select(CashFlowStatementPeriod).where(
                    CashFlowStatementPeriod.store_id == args.store_id,
                    CashFlowStatementPeriod.period_begin <= date_to,
                    CashFlowStatementPeriod.period_end >= date_from,
                ).order_by(CashFlowStatementPeriod.period_begin)
            )
        )
        if not periods:
            print("    (нет ни одного периода Ozon, пересекающегося с этим диапазоном — "
                  "cash-flow, похоже, ещё не синхронизировался за эти даты)")
        ad_total = 0.0
        all_names: dict[str, float] = {}
        covered_days: set[date] = set()
        excluded_any = False
        for p in periods:
            fully_contained = p.period_begin >= date_from and p.period_end <= date_to
            label = "УЧТЁН" if fully_contained else "ИСКЛЮЧЁН (частично пересекает — период нельзя разрезать)"
            print(f"    Период {p.period_begin} — {p.period_end} (services_total={p.services_total}) — {label}:")
            if not fully_contained:
                excluded_any = True
                print("        (статьи не выводятся и не суммируются — см. предупреждение ниже)")
                continue
            d = p.period_begin
            while d <= p.period_end:
                covered_days.add(d)
                d += timedelta(days=1)
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

        requested_days = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]
        uncovered_days = sorted(d for d in requested_days if d not in covered_days)
        if excluded_any or uncovered_days:
            print()
            print(f"    ⚠ Cash-flow покрывает НЕ весь запрошенный диапазон — учтённые периоды "
                  f"покрывают {len(covered_days)} из {(date_to - date_from).days + 1} дней.")
            if uncovered_days:
                print(f"    ⚠ Дни БЕЗ учтённых cash-flow данных: {uncovered_days[0]} — {uncovered_days[-1]} "
                      f"({len(uncovered_days)} дн.)")

        print()
        print(f"    ИТОГО по статьям, похожим на рекламу ({'/'.join(AD_NAME_HINTS)}): {round(ad_total, 2)} "
              "(отрицательное — это НОРМАЛЬНО, в cash-flow суммы хранятся как списания)")

        print()
        print("=" * 70)
        print("СРАВНЕНИЕ (по модулю — знаки у двух источников разные по смыслу, не по ошибке):")
        auto_abs = abs(float(auto_total))
        ad_abs = abs(ad_total)
        print(f"    Performance API за ВЕСЬ запрошенный диапазон, |spend_rub|: {auto_abs}")
        print(f"    Cash-flow за УЧТЁННЫЕ периоды (см. ⚠ выше, если диапазон покрыт не полностью), "
              f"|похоже на рекламу|: {round(ad_abs, 2)}")
        if uncovered_days:
            print("    Эти два числа НЕ сравнивайте напрямую — разные диапазоны дней. "
                  "Честное сравнение — по дням, реально покрытым cash-flow, ниже:")
            auto_abs_covered = abs(sum(float(total) for d, total in by_day if d in covered_days))
            print(f"    Performance API ЗА ТЕ ЖЕ {len(covered_days)} дн., что покрыл cash-flow: {auto_abs_covered}")
            if auto_abs_covered and ad_abs:
                print(f"    Разница (честная, по одному диапазону): {round(ad_abs - auto_abs_covered, 2)} "
                      f"({round((ad_abs - auto_abs_covered) / auto_abs_covered * 100, 2)}%)")
        elif auto_abs and ad_abs:
            print(f"    Разница: {round(ad_abs - auto_abs, 2)} ({round((ad_abs - auto_abs) / auto_abs * 100, 2)}%)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
