"""Read-only diagnostic: for a given store/date range, prints "Комиссия
Ozon" and "Выручка (выкуп)" from BOTH Ozon sources this app touches, side
by side:

  1. OrderDailyStatistic (Ozon Seller API postings — FBO: POST
     /v2/posting/fbo/list, FBS: POST /v3/posting/fbs/list), summed by each
     posting's own `in_process_at` (order-processing-entry) date — this is
     what MarginBlock on the Дашборд shows as "Выручка (выкуп)"
     (delivered_sum_rub) and the commission half of "Маржа"
     (commission_rub, summed from financial_data.products[].commission_amount
     per product line, regardless of the posting's own status — see
     order_daily_sync_service.py's aggregate_postings_by_day()).
  2. CashFlowStatementPeriod (Ozon Seller API POST /v1/finance/cash-flow-
     statement/list — the ACCRUAL/ДДС system, weekly periods on Ozon's own
     payout cycle) — its own `commission_amount`/`orders_amount` fields,
     deliberately NOT surfaced on the Дашборд today (see that model's own
     docstring: "commission_amount would duplicate MarginBlock's own
     commission from postings" — a design assumption made BEFORE this gap
     was found, not a confirmed equivalence).

Why this script exists: a real account's Дашборд showed "Комиссия Ozon" =
-2 537 673.34 ₽ for 2026-09-01..09-12, while the SAME account's manually
exported Ozon "Начисления" report showed -4 188 072.00 ₽ for the same
period — a real ~1 650 399 ₽ (~39%) gap. "Выручка (выкуп)" showed a
smaller but still real gap (5 005 545 ₽ vs 4 767 738.35 ₽, ~237 807 ₽).
Two plausible, UNCONFIRMED explanations this script is built to
distinguish between (not to assume):
  a) A genuine scope difference between the two Ozon systems — same
     precedent as the already-confirmed ad-spend gap (Performance API's
     per-click-date statistics vs cash-flow's own weekly billing periods
     don't line up 1:1) and the logistics/services gap (services.total
     mixing several categories with no per-category subtotal). The
     "Начисления" report the user exports may simply BE the cash-flow
     system's own accrual figures, not a re-statement of postings data.
  b) A real bug in how postings are aggregated: aggregate_postings_by_day()
     buckets EVERY posting by `in_process_at` (when it entered processing
     at Ozon), not by delivery/acceptance date — a boundary-week mismatch
     would show up as a gap that shrinks/grows depending on exactly which
     dates are requested. Also: postings with no `in_process_at` at all are
     silently dropped from every day's totals (see SyncOutcome.
     skipped_no_process_date's own docstring) — if a real chunk of orders
     for this exact window had that field empty at sync time, this would
     under-count silently.

This script does NOT decide between (a) and (b) — it just prints both
sources' real numbers, prorated the same way the Дашборд's own Логистика
block already prorates partial cash-flow periods, so the actual gap (and
whether it tracks (a) or (b)) can be read off directly instead of guessed.

No Ozon API call — reads only what previous syncs already saved (both the
orders auto-sync and the cash-flow-statement auto-sync must have already
run for the store/period in question), so it's free to re-run.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/compare_commission_and_revenue_sources.py \\
        --store-id <id> --date-from 2026-09-01 --date-to 2026-09-12
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.cash_flow_statement_period import CashFlowStatementPeriod  # noqa: E402
from app.models.order_daily_statistic import OrderDailyStatistic  # noqa: E402
from app.models.store import Store  # noqa: E402


def _resolve_store_id(db, *, store_id: str | None, store_name: str | None) -> str | None:
    """--store-id is the internal UUID (Store.id) — NOT Ozon's own numeric
    Client-Id, which looks similar (a plain number) but matches nothing
    here and silently returns zero rows rather than an error. --store-name
    sidesteps needing that UUID at all: a case-insensitive substring match
    against Store.name, run right here."""
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


def _period_overlap_fraction(period_begin: date, period_end: date, date_from: date, date_to: date) -> float:
    """Same day-count proration as dashboard_service.py's own
    _period_overlap_fraction — mirrors it here rather than importing a
    private helper, matching every other diagnostic script's convention of
    staying self-contained."""
    overlap_start = max(period_begin, date_from)
    overlap_end = min(period_end, date_to)
    if overlap_start > overlap_end:
        return 0.0
    overlap_days = (overlap_end - overlap_start).days + 1
    period_days = (period_end - period_begin).days + 1
    return overlap_days / period_days if period_days > 0 else 0.0


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

        print("=" * 70)
        print(f"1) OrderDailyStatistic (постинги FBO+FBS, группировка по in_process_at) — {date_from} — {date_to}")
        print("=" * 70)
        rows = (
            db.query(OrderDailyStatistic)
            .filter(
                OrderDailyStatistic.store_id == store_id,
                OrderDailyStatistic.date >= date_from,
                OrderDailyStatistic.date <= date_to,
            )
            .order_by(OrderDailyStatistic.date)
            .all()
        )
        if not rows:
            print("    (нет ни одной строки за этот период — проверьте, что автосбор заказов запускался)")
        distinct_days = sorted({r.date for r in rows})
        requested_days = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]
        missing_days = [d for d in requested_days if d not in distinct_days]
        for schema in ("FBO", "FBS"):
            schema_rows = [r for r in rows if r.delivery_schema == schema]
            commission = round(sum(float(r.commission_rub or 0) for r in schema_rows), 2)
            delivered_sum = round(sum(float(r.delivered_sum_rub or 0) for r in schema_rows), 2)
            delivered_units = sum(r.delivered_units or 0 for r in schema_rows)
            ordered_sum_discounted = round(sum(float(r.ordered_sum_discounted_rub or 0) for r in schema_rows), 2)
            print(f"    {schema}: строк(дней)={len(schema_rows)}  commission_rub={commission}  "
                  f"delivered_sum_rub={delivered_sum}  delivered_units={delivered_units}  "
                  f"ordered_sum_discounted_rub={ordered_sum_discounted}")
        commission_total = round(sum(float(r.commission_rub or 0) for r in rows), 2)
        delivered_sum_total = round(sum(float(r.delivered_sum_rub or 0) for r in rows), 2)
        print(f"    ИТОГО (как на Дашборде): commission_rub={commission_total}  delivered_sum_rub={delivered_sum_total}")
        if missing_days:
            print(f"    ⚠ Дней БЕЗ строк OrderDailyStatistic в запрошенном диапазоне: {len(missing_days)} "
                  f"({missing_days[0]} — {missing_days[-1]}) — это может означать, что синк ещё не покрыл эти "
                  "дни, ЛИБО что все постинги этих дней были без in_process_at и потому отброшены "
                  "(см. SyncOutcome.skipped_no_process_date) — сверьтесь с последним SyncRun.error_message.")

        print()
        print("=" * 70)
        print(f"2) CashFlowStatementPeriod (ДДС, недельные периоды) — периоды, ПЕРЕСЕКАЮЩИЕСЯ с {date_from} — {date_to}")
        print("=" * 70)
        periods = list(
            db.scalars(
                select(CashFlowStatementPeriod).where(
                    CashFlowStatementPeriod.store_id == store_id,
                    CashFlowStatementPeriod.period_begin <= date_to,
                    CashFlowStatementPeriod.period_end >= date_from,
                ).order_by(CashFlowStatementPeriod.period_begin)
            )
        )
        if not periods:
            print("    (нет ни одного периода Ozon, пересекающегося с этим диапазоном — "
                  "cash-flow, похоже, ещё не синхронизировался за эти даты)")
        commission_prorated = 0.0
        orders_prorated = 0.0
        item_delivery_return_prorated = 0.0
        is_estimated = False
        for p in periods:
            fraction = _period_overlap_fraction(p.period_begin, p.period_end, date_from, date_to)
            if fraction < 1.0:
                is_estimated = True
            commission_prorated += float(p.commission_amount or 0) * fraction
            orders_prorated += float(p.orders_amount or 0) * fraction
            item_delivery_return_prorated += float(p.item_delivery_and_return_amount or 0) * fraction
            print(f"    Период {p.period_begin} — {p.period_end} (доля в диапазоне={round(fraction, 3)}): "
                  f"commission_amount={p.commission_amount}  orders_amount={p.orders_amount}  "
                  f"returns_amount={p.returns_amount}  item_delivery_and_return_amount={p.item_delivery_and_return_amount}")
        commission_prorated = round(commission_prorated, 2)
        orders_prorated = round(orders_prorated, 2)
        item_delivery_return_prorated = round(item_delivery_return_prorated, 2)
        print(f"    ИТОГО (с пропорцией по дням для частичных периодов): commission_amount={commission_prorated}  "
              f"orders_amount={orders_prorated}  item_delivery_and_return_amount={item_delivery_return_prorated}")
        if is_estimated:
            print("    ⚠ Хотя бы один период пересекает диапазон только ЧАСТИЧНО — цифры выше оценочные "
                  "(линейная пропорция по дням, как и в самом Дашборде).")

        print()
        print("=" * 70)
        print("СРАВНЕНИЕ (по модулю — разный знак у источников не является ошибкой):")
        print(f"    Комиссия — OrderDailyStatistic (Дашборд): |{commission_total}| = {abs(commission_total)}")
        print(f"    Комиссия — CashFlowStatementPeriod (ДДС):  |{commission_prorated}| = {abs(commission_prorated)}")
        if commission_total:
            diff = round(abs(commission_prorated) - abs(commission_total), 2)
            print(f"    Разница: {diff} ({round(diff / abs(commission_total) * 100, 2)}%)")
        print()
        print(f"    Выручка — OrderDailyStatistic.delivered_sum_rub (Дашборд): |{delivered_sum_total}| = {abs(delivered_sum_total)}")
        print(f"    Выручка — CashFlowStatementPeriod.orders_amount (ДДС):     |{orders_prorated}| = {abs(orders_prorated)}")
        if delivered_sum_total:
            diff = round(abs(orders_prorated) - abs(delivered_sum_total), 2)
            print(f"    Разница: {diff} ({round(diff / abs(delivered_sum_total) * 100, 2)}%)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
