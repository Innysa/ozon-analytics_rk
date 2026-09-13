"""One-off diagnostic: in a SINGLE run, probes BOTH

  1. POST /v1/finance/accrual/by-day — candidate source for TRUE DAILY
     Комиссия/Выручка figures (unlike /v2/finance/realization, which is
     monthly-only and 404s "Report was not found" for anything not yet a
     full closed month — confirmed 2026-09-13, see RealizationReportMonth's
     own docstring). This method's request/response contract is NOT yet
     confirmed at all — it was only seen in this account's own method-name
     listing (see debug_cash_flow_statement.py's own docstring for that
     list) and one earlier round got "invalid value for string field date"
     trying `{"date": {...}}`, meaning "date" wants a plain value there,
     not an object — never actually called successfully. This script tries
     two plausible bodies in turn and prints whichever succeeds, or both
     validation errors if neither does (Ozon's own error text has
     repeatedly named the real required field in this project — e.g. the
     historical ReviewListRequest.Limit case — so a failure here is still a
     useful, real answer, not a dead end).

  2. POST /v2/finance/realization — the confirmed-working monthly
     settlement report (see RealizationReportMonth's own docstring for the
     confirmed {"year", "month"} request shape). Only a truncated tail of
     one real response has been seen so far; this call exists here
     specifically to capture the FULL shape (top-level fields, first item
     in full, item count) in one condensed, non-scrolling summary.

Both raw responses are saved to files (--out-dir) and NEVER dumped in full
to the console — only a condensed summary (top-level keys, item count,
ONE full example item, and a few candidate running sums) — because the
target console for this project has no scrollback/copy (see
inspect_cash_flow_periods.py's own --find-key flag, built for the exact
same constraint).

No parsing, no storage — purely to capture real shapes before anything
gets built on top of them.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/probe_accrual_and_realization.py \\
        --store-id <id> --accrual-date 2026-09-12 --realization-year 2026 --realization-month 8

    # or by name instead of the UUID, and a custom output directory:
    docker compose exec app python backend/scripts/probe_accrual_and_realization.py \\
        --store-name "Комфорт дом" --accrual-date 2026-09-12 \\
        --realization-year 2026 --realization-month 8 --out-dir /tmp/ozon_probe
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.encryption import decrypt_secret  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.ozon_credentials import OzonCredentials  # noqa: E402
from app.models.store import Store  # noqa: E402
from app.services.ozon.client import OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402
from app.services.ozon.exceptions import OzonAPIError  # noqa: E402


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


def _to_num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _find_item_list(obj, path=()):
    """Recursively finds the first list whose elements are mostly dicts —
    a generic stand-in for "the items array", since the real top-level key
    holding it (e.g. "rows", "items", "result.rows", ...) is NOT confirmed
    for either method here. Returns (dotted path string, the list) or
    (None, None) if nothing list-shaped was found."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return ".".join(path) or "(корень)", obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            found_path, found_list = _find_item_list(value, path + (key,))
            if found_list is not None:
                return found_path, found_list
    return None, None


def _print_condensed(label: str, data: dict, out_file: Path, sum_fields: list[str]) -> None:
    out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    Полный сырой ответ сохранён в {out_file}")

    top_level_preview = {
        k: (f"<{type(v).__name__}, len={len(v)}>" if isinstance(v, (list, dict)) else v) for k, v in data.items()
    }
    print(f"    Верхнеуровневые поля {label}: {json.dumps(top_level_preview, ensure_ascii=False)}")

    item_path, items = _find_item_list(data)
    if items is None:
        print("    Список позиций не найден нигде во вложенной структуре (структура ответа отличается от ожидаемой).")
        return
    print(f"    Список позиций найден по пути: {item_path}  (позиций: {len(items)})")
    print("    ПЕРВАЯ позиция целиком:")
    print(json.dumps(items[0], ensure_ascii=False, indent=2))

    for field_path in sum_fields:
        keys = field_path.split(".")
        total = 0.0
        seen = 0
        for item in items:
            value = item
            for key in keys:
                if isinstance(value, dict):
                    value = value.get(key)
                else:
                    value = None
                    break
            if value is not None:
                seen += 1
            total += _to_num(value)
        print(f"    Сумма '{field_path}' по всем позициям: {round(total, 2)} (поле присутствовало в {seen} из {len(items)} позиций)")

    if any(isinstance(i, dict) and "quantity" in i for i in items):
        total_rev = sum(_to_num(i.get("seller_price_per_instance")) * _to_num(i.get("quantity", 1)) for i in items)
        print(f"    Сумма 'seller_price_per_instance * quantity': {round(total_rev, 2)}")
    else:
        total_rev = sum(_to_num(i.get("seller_price_per_instance")) for i in items)
        print(
            f"    Поле 'quantity' НЕ найдено ни в одной позиции — 'quantity' НЕ подтверждено, "
            f"взята сумма одного 'seller_price_per_instance' на позицию (без умножения): {round(total_rev, 2)}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None, help="внутренний UUID магазина")
    parser.add_argument("--store-name", default=None, help="имя магазина — альтернатива --store-id")
    parser.add_argument("--accrual-date", required=True, help="ГГГГ-ММ-ДД — день для /v1/finance/accrual/by-day")
    parser.add_argument("--realization-year", type=int, required=True)
    parser.add_argument("--realization-month", type=int, required=True)
    parser.add_argument("--out-dir", default="/tmp", help="куда сохранить полные сырые JSON-файлы (по умолчанию /tmp)")
    args = parser.parse_args()
    if not args.store_id and not args.store_name:
        print("Укажите --store-id или --store-name.")
        return

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        store_id = _resolve_store_id(db, store_id=args.store_id, store_name=args.store_name)
        if not store_id:
            return

        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == store_id).first()
        if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
            print("Для этого магазина не заданы ключи Ozon Seller API.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            print("=" * 70)
            print(f"1) POST /v1/finance/accrual/by-day — дата={args.accrual_date}")
            print("=" * 70)
            accrual_bodies = [
                ("date как строка + пагинация", {"date": args.accrual_date, "page": 1, "page_size": 1000}),
                ("date как диапазон (по образцу ДДС)", {"date": {"from": args.accrual_date, "to": args.accrual_date}, "page": 1, "page_size": 1000}),
            ]
            accrual_succeeded = False
            for label, body in accrual_bodies:
                print(f"    Пробую вариант «{label}»: {json.dumps(body, ensure_ascii=False)}")
                try:
                    data = client.probe_finance_endpoint("/v1/finance/accrual/by-day", body)
                except OzonAPIError as exc:
                    print(f"    {type(exc).__name__}: {exc}")
                    continue
                print(f"    УСПЕХ с вариантом «{label}»")
                _print_condensed(
                    "/v1/finance/accrual/by-day",
                    data,
                    out_dir / f"accrual_by_day_{args.accrual_date}.json",
                    sum_fields=["amount", "commission", "total"],
                )
                accrual_succeeded = True
                break
            if not accrual_succeeded:
                print("    Ни один вариант тела запроса не сработал — оба ответа Ozon показаны выше как есть.")

            print()
            print("=" * 70)
            print(f"2) POST /v2/finance/realization — год={args.realization_year}, месяц={args.realization_month}")
            print("=" * 70)
            try:
                data = client.get_realization_report(year=args.realization_year, month=args.realization_month)
            except OzonAPIError as exc:
                print(f"    {type(exc).__name__}: {exc}")
            else:
                _print_condensed(
                    "/v2/finance/realization",
                    data,
                    out_dir / f"realization_{args.realization_year}_{args.realization_month:02d}.json",
                    sum_fields=[
                        "delivery_commission.total",
                        "delivery_commission.commission",
                        "delivery_commission.bonus",
                        "delivery_commission.standard_fee",
                    ],
                )
    finally:
        db.close()


if __name__ == "__main__":
    main()
