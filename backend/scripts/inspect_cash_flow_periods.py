"""Read-only diagnostic: dumps the CashFlowStatementPeriod rows actually
stored in the database for a store, plus what the Дашборд's "Логистика и
услуги" block would compute from them for a given date range.

No Ozon API call — this only reads what a previous sync already saved, so
it costs nothing against any Ozon rate limit and is safe to re-run freely.

Why this script exists: a real account's Дашборд showed "Прочие услуги"
as a large POSITIVE number (+7 144 435,66 ₽), but every confirmed real
`services.total` value seen in the original diagnostic
(debug_cash_flow_statement.py) was NEGATIVE and no single week's magnitude
exceeded ~640 000 ₽. Re-reading dashboard_service.py's aggregation
(`other_services_rub=round(sum(float(p.services_total or 0) for p in
periods_in_range), 2)`) and cash_flow_statement_sync_service.py's parsing
(`record.services_total = services.get("total")`) found no sign-flip or
field-mixup bug in the code itself — so the next step is to look at what
is actually stored, not guess further from the code alone.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/inspect_cash_flow_periods.py \\
        --store-id <id> > cash_flow_periods_dump.txt

Prints, in order:
  1. Every stored CashFlowStatementPeriod row for the store (all columns
     used by the Дашборд), oldest first.
  2. Whether any two rows share the exact same period_begin/period_end
     (would violate the DB's own unique constraint, so should be
     impossible — printed anyway as a sanity check).
  3. The exact sum the Дашборд would show for --date-from/--date-to (same
     "fully contained period" rule as dashboard_service.py's
     _cash_flow_periods_in_range + compute_dashboard's logistics block),
     defaulting to the same last-30-days window the Дашборд itself
     defaults to when no dates are picked.
  4. Every distinct item `name` seen across services_items_json,
     delivery_services_items_json and delivery_return_items_json for this
     store, each with its min/max observed `price` — a direct, complete
     answer to "does anything look like a fine/penalty by name", instead
     of relying on partial screenshots of a few periods.

Pass --period-begin ГГГГ-ММ-ДД to additionally dump raw_payload (the exact
{"cash_flow", "details"} JSON Ozon returned) for the one row whose
period_begin matches — use this BEFORE re-syncing to capture what Ozon
actually sent for a row that looks wrong, since a re-sync will overwrite
it (the sync always UPSERTs by period_begin/period_end, so this evidence
is gone once the row is refreshed).

Pass --find-key SUBSTRING instead when you know an item's `name` (e.g.
from a manually exported Ozon report) but not where it lives in the raw
JSON tree, and can't scroll/copy a full JSON dump (e.g. over a VNC
console) — this searches EVERY stored period's raw_payload recursively
for any object whose "name" contains SUBSTRING and prints only the short
dotted path to it (e.g. "details.delivery.return_services.items[0]") plus
its price, one line per match, no full JSON. Skips all other output when
given, to keep the result to a handful of lines.

Pass --find-price A,B,C instead when a manually exported Ozon report gives
you a RUB amount for a line item but the report's own Russian description
(e.g. "Обработка отправления Drop-off партнёрами (ПВЗ)") isn't itself a
raw JSON `name` you can grep for — this searches EVERY stored period's
raw_payload recursively for any object with BOTH a "name" and a "price"
where price matches one of the given values within 0.01, and prints the
dotted path, the CONFIRMED real name, and the price. Same VNC-console
motivation and one-match-per-line output as --find-key, but keyed by
amount instead of a guessed name substring — this project's discipline is
to never assume an item's raw name from its human-readable report label.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.cash_flow_statement_period import CashFlowStatementPeriod  # noqa: E402

DASHBOARD_DEFAULT_LOOKBACK_DAYS = 30  # mirrors app.core.config.Settings.DASHBOARD_DEFAULT_LOOKBACK_DAYS


def _find_paths(obj, needle: str, path: list[str]) -> list[tuple[str, dict]]:
    """Recursively walks obj (nested dicts/lists — exactly the shape a
    json.loads'd raw_payload has) looking for any dict with a "name" field
    containing `needle` (case-insensitive). Returns [(dotted path, the
    matching dict itself), ...] — the path uses "a.b.c" / "a.b[2]" style,
    short enough for a one-line answer instead of a full JSON dump."""
    results: list[tuple[str, dict]] = []
    if isinstance(obj, dict):
        name = obj.get("name")
        if isinstance(name, str) and needle.lower() in name.lower():
            results.append((".".join(path) or "(корень)", obj))
        for key, value in obj.items():
            results.extend(_find_paths(value, needle, path + [key]))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            new_path = path[:-1] + [f"{path[-1]}[{i}]"] if path else [f"[{i}]"]
            results.extend(_find_paths(value, needle, new_path))
    return results


def _find_by_price(obj, targets: list[float], path: list[str], tol: float = 0.01) -> list[tuple[str, dict]]:
    """Same idea as _find_paths, but matches on `price` (within `tol`)
    instead of a `name` substring — for confirming an item's real raw name
    from a rub amount already known to be correct, rather than guessing the
    name from a human-readable report label."""
    results: list[tuple[str, dict]] = []
    if isinstance(obj, dict):
        price = obj.get("price")
        name = obj.get("name")
        if isinstance(price, (int, float)) and isinstance(name, str) and any(abs(price - t) <= tol for t in targets):
            results.append((".".join(path) or "(корень)", obj))
        for key, value in obj.items():
            results.extend(_find_by_price(value, targets, path + [key], tol))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            new_path = path[:-1] + [f"{path[-1]}[{i}]"] if path else [f"[{i}]"]
            results.extend(_find_by_price(value, targets, new_path, tol))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД, по умолчанию — как на Дашборде (30 дней назад)")
    parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД, по умолчанию — сегодня")
    parser.add_argument("--period-begin", default=None, help="ГГГГ-ММ-ДД — если задан, дополнительно печатает raw_payload для этого периода")
    parser.add_argument(
        "--find-key", default=None,
        help="подстрока в поле 'name' статьи (например ReturnFlowLogistic) — печатает ТОЛЬКО короткий путь "
             "вложенности до неё по всем сохранённым периодам, без полного JSON (для VNC-консолей без скролла/копирования)",
    )
    parser.add_argument(
        "--find-price", default=None,
        help="список сумм через запятую (например -3450,-1140,-50), известных из выгрузки Ozon — печатает "
             "путь, РЕАЛЬНОЕ имя статьи (name) и цену для каждого совпадения (допуск 0.01), без полного JSON. "
             "Используйте, когда есть точная сумма из отчёта, но нет уверенности в raw name статьи.",
    )
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    date_to = date.fromisoformat(args.date_to) if args.date_to else today
    date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=DASHBOARD_DEFAULT_LOOKBACK_DAYS - 1)

    db = SessionLocal()
    try:
        rows = (
            db.query(CashFlowStatementPeriod)
            .filter(CashFlowStatementPeriod.store_id == args.store_id)
            .order_by(CashFlowStatementPeriod.period_begin)
            .all()
        )

        if args.find_key:
            print(f"Поиск статьи с 'name', содержащим {args.find_key!r}, по всем сохранённым периодам:")
            found_any = False
            for r in rows:
                if not r.raw_payload:
                    continue
                raw = json.loads(r.raw_payload)
                for path, item in _find_paths(raw, args.find_key, []):
                    found_any = True
                    print(f"    {r.period_begin} — {r.period_end}: {path}  price={item.get('price')!r}")
            if not found_any:
                print("    Не найдено ни в одном сохранённом периоде (проверьте написание подстроки).")
            return

        if args.find_price:
            targets = [float(x) for x in args.find_price.split(",")]
            print(f"Поиск статей с ценой в {targets!r} (допуск 0.01), по всем сохранённым периодам:")
            found_any = False
            for r in rows:
                if not r.raw_payload:
                    continue
                raw = json.loads(r.raw_payload)
                for path, item in _find_by_price(raw, targets, []):
                    found_any = True
                    print(f"    {r.period_begin} — {r.period_end}: {path}  name={item.get('name')!r}  price={item.get('price')!r}")
            if not found_any:
                print("    Не найдено ни в одном сохранённом периоде (проверьте суммы и допуск).")
            return

        print("=" * 70)
        print(f"Всего сохранённых периодов для магазина {args.store_id}: {len(rows)}")
        print("=" * 70)
        for r in rows:
            print(
                f"{r.period_begin} — {r.period_end}  "
                f"orders={r.orders_amount} returns={r.returns_amount} commission={r.commission_amount} "
                f"services_amount(cash_flows)={r.services_amount} idr={r.item_delivery_and_return_amount}"
            )
            print(
                f"    delivery_services_total(Логистика)={r.delivery_services_total}  "
                f"delivery_return_total(Возврат)={r.delivery_return_total}  "
                f"services_total(Прочие услуги)={r.services_total}  "
                f"begin_balance_amount={r.begin_balance_amount}"
            )
            print(f"    created_at={r.created_at}  updated_at={r.updated_at}  source={r.source}")

        print()
        print("=" * 70)
        print("Проверка дублей периода (не должно быть ни одного — ограничение БД):")
        seen: dict[tuple, int] = {}
        for r in rows:
            key = (r.period_begin, r.period_end)
            seen[key] = seen.get(key, 0) + 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        print(f"    дублей найдено: {len(dupes)}" + (f" — {dupes}" if dupes else ""))

        print()
        print("=" * 70)
        print(f"Что покажет Дашборд за диапазон {date_from} — {date_to} "
              "(только периоды Ozon, ЦЕЛИКОМ входящие в этот диапазон):")
        periods_in_range = [r for r in rows if r.period_begin >= date_from and r.period_end <= date_to]
        print(f"    периодов целиком внутри диапазона: {len(periods_in_range)}")
        for r in periods_in_range:
            print(f"    -> {r.period_begin} — {r.period_end}: services_total={r.services_total}")
        logistics_sum = round(sum(float(r.delivery_services_total or 0) for r in periods_in_range), 2)
        returns_sum = round(sum(float(r.delivery_return_total or 0) for r in periods_in_range), 2)
        services_sum = round(sum(float(r.services_total or 0) for r in periods_in_range), 2)
        print(f"    ИТОГО Логистика={logistics_sum}  Возврат={returns_sum}  Прочие услуги={services_sum}")

        print()
        print("=" * 70)
        print("Все уникальные названия статей (services/delivery_services/delivery_return) "
              "по ВСЕМ сохранённым периодам, с мин/макс наблюдаемой суммой:")
        field_map = {
            "services_items_json (Прочие услуги)": "services_items_json",
            "delivery_services_items_json (Логистика)": "delivery_services_items_json",
            "delivery_return_items_json (Возврат)": "delivery_return_items_json",
        }
        for label, attr in field_map.items():
            names: dict[str, list[float]] = {}
            for r in rows:
                raw = getattr(r, attr)
                if not raw:
                    continue
                try:
                    items = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                for item in items:
                    name = item.get("name")
                    price = item.get("price")
                    if name is None:
                        continue
                    names.setdefault(name, []).append(price)
            print(f"  {label}:")
            if not names:
                print("    (нет данных)")
            for name, prices in sorted(names.items()):
                numeric = [p for p in prices if isinstance(p, (int, float))]
                lo = min(numeric) if numeric else None
                hi = max(numeric) if numeric else None
                print(f"    {name}: встречалось {len(prices)} раз(а), min={lo}, max={hi}")

        if args.period_begin:
            target = date.fromisoformat(args.period_begin)
            match = next((r for r in rows if r.period_begin == target), None)
            print()
            print("=" * 70)
            print(f"raw_payload для периода, начинающегося {target}:")
            if match is None:
                print(f"    период с period_begin={target} не найден для этого магазина.")
            elif not match.raw_payload:
                print("    raw_payload пуст для этой записи (возможно, сохранена до того, как это поле начали заполнять).")
            else:
                raw = json.loads(match.raw_payload)
                print()
                print("=" * 70)
                print("details.delivery ЦЕЛИКОМ, без обрезки (чтобы не гадать по grep-окну, "
                      "где именно вложен return_services/return/etc.):")
                delivery = (raw.get("details") or {}).get("delivery") or {}
                print(json.dumps(delivery, ensure_ascii=False, indent=2))
                print()
                print("=" * 70)
                print("Что ИЗ ЭТОГО делает ТЕКУЩИЙ код (delivery.return + delivery.return_services, "
                      "если это поменяется в будущем — сверяйте с cash_flow_statement_sync_service.py):")
                delivery_return = delivery.get("return") or {}
                delivery_return_services = delivery.get("return_services") or {}
                print(f"    delivery.get('return').get('total') = {delivery_return.get('total')!r}")
                print(f"    delivery.get('return_services').get('total') = {delivery_return_services.get('total')!r}")
                print(f"    (пусто {{}} означает: такого ключа НЕТ на этом уровне вложенности вообще)")
                print()
                print("=" * 70)
                print("Сохранённое в БД для ЭТОЙ записи (что реально сейчас в delivery_return_total):")
                print(f"    delivery_return_total = {match.delivery_return_total!r}")
                print(f"    delivery_return_items_json = {match.delivery_return_items_json!r}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
