"""Read-only diagnostic: for a store, prints which calendar days in
[date_from, date_to] have NO OrderDailyStatistic row at all — per
fulfillment scheme (FBO, FBS) separately, since a day can have one
scheme's row synced but not the other's, grouped into consecutive date
RANGES so the output can be pasted straight into the "Обновить заказы
(авто)" date pickers on the РНП page.

Why this script exists: a SyncRun's error_message doesn't always name the
exact date range that failed. CONFIRMED 2026-09-12 on a real account
("Дельта дом", store 4d011cd9-3cce-4935-b124-6c7998d5fb98): an older
partial run (2026-09-11 03:30 UTC) recorded only "FBO: Ozon вернул 429
Too Many Requests" — no chunk dates at all, unlike newer runs (see
sync_order_daily_statistics's own per-chunk error text, which DOES
include "{schema} {chunk_from}—{chunk_to}: ..."). That older run most
likely predates whatever the deployed code's chunking/error-format was
at that exact moment — reading its stored error_message text can't
recover information it never wrote in the first place. The only
reliable way to find the real gap is to stop trying to parse
error_message and instead look at what's actually in
OrderDailyStatistic: which calendar days genuinely have a row and which
don't.

No Ozon API call — reads only what previous syncs already saved, so it's
free to re-run.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/find_missing_order_days.py \\
        --store-id <id> --date-from 2026-08-13 --date-to 2026-09-11

    # or by name instead of the UUID:
    docker compose exec app python backend/scripts/find_missing_order_days.py \\
        --store-name "Дельта дом" --date-from 2026-08-13 --date-to 2026-09-11

date_from/date_to default to the same 30-day lookback the orders sync
itself defaults to (ORDER_STATS_DEFAULT_LOOKBACK_DAYS) when omitted.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.order_daily_statistic import OrderDailyStatistic  # noqa: E402
from app.models.store import Store  # noqa: E402


def _resolve_store_id(db, *, store_id: str | None, store_name: str | None) -> str | None:
    """--store-id is the internal UUID (Store.id) — NOT Ozon's own numeric
    Client-Id. --store-name sidesteps needing that UUID at all: a
    case-insensitive substring match against Store.name."""
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


def _group_consecutive_dates(missing: list[date]) -> list[tuple[date, date]]:
    """Turns a sorted list of individual missing dates into consecutive
    (start, end) ranges — so 10 missing single days that happen to be one
    unbroken 10-day gap print as ONE range, directly usable as one
    "Обновить заказы (авто)" date-picker selection, not ten separate ones."""
    if not missing:
        return []
    ranges: list[tuple[date, date]] = []
    range_start = missing[0]
    prev = missing[0]
    for d in missing[1:]:
        if (d - prev).days > 1:
            ranges.append((range_start, prev))
            range_start = d
        prev = d
    ranges.append((range_start, prev))
    return ranges


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None, help="внутренний UUID магазина")
    parser.add_argument("--store-name", default=None, help="имя магазина — альтернатива --store-id")
    parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД, по умолчанию — как у синка заказов (30 дней назад)")
    parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД, по умолчанию — сегодня")
    args = parser.parse_args()
    if not args.store_id and not args.store_name:
        print("Укажите --store-id или --store-name.")
        return

    settings = get_settings()
    today = date.today()
    date_to = date.fromisoformat(args.date_to) if args.date_to else today
    date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=settings.ORDER_STATS_DEFAULT_LOOKBACK_DAYS - 1)
    requested_days = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]

    db = SessionLocal()
    try:
        store_id = _resolve_store_id(db, store_id=args.store_id, store_name=args.store_name)
        if not store_id:
            return

        rows = (
            db.query(OrderDailyStatistic.date, OrderDailyStatistic.delivery_schema)
            .filter(
                OrderDailyStatistic.store_id == store_id,
                OrderDailyStatistic.date >= date_from,
                OrderDailyStatistic.date <= date_to,
            )
            .all()
        )
        present_by_schema: dict[str, set[date]] = {"FBO": set(), "FBS": set()}
        for d, schema in rows:
            present_by_schema.setdefault(schema, set()).add(d)

        print("=" * 70)
        print(f"Диапазон проверки: {date_from} — {date_to} ({len(requested_days)} дн.)")
        print("=" * 70)
        any_missing = False
        for schema in ("FBO", "FBS"):
            present = present_by_schema.get(schema, set())
            missing = sorted(d for d in requested_days if d not in present)
            print(f"\n{schema}: строк найдено {len(present)} из {len(requested_days)} дней.")
            if not missing:
                print(f"    Пропусков нет — все {len(requested_days)} дней засинканы.")
                continue
            any_missing = True
            ranges = _group_consecutive_dates(missing)
            print(f"    Пропущено дней: {len(missing)}, диапазонов: {len(ranges)}")
            for start, end in ranges:
                if start == end:
                    print(f"    -> {start}  (1 день)")
                else:
                    print(f"    -> {start} — {end}  ({(end - start).days + 1} дн.)")

        if any_missing:
            print(
                "\nКаждый диапазон выше можно подставить напрямую в date-picker'ы "
                "«Обновить заказы (авто)» на странице РНП (или в date_from/date_to "
                "запроса POST .../sync/ozon-orders) — по одному запуску на диапазон."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
