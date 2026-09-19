"""One-off diagnostic: probes THREE Ozon Seller API methods this project's
own method list (docs/ozon-seller-api-methods.md) already named as
available on this account but has NEVER actually called:

  1. POST /v1/finance/realization/by-day — "реализация по дням" (realization
     BY DAY). find_accrual_commission_field.py's CLOSED investigation
     (2026-09-14) only ever tried /v1/finance/accrual/by-day for revenue and
     found no summable field there — it never tried this DIFFERENT, more
     literally-named method. NEW EVIDENCE justifying reopening the question
     (per that script's own "do not re-run without new evidence" warning):
     a real account's own "Начисления"/"Логистика" XLSX cabinet exports
     (2026-09-19, 01.09-19.09 period) show a full "Группа услуг"/"Тип
     начисления" breakdown (Продажи, Возвраты, Вознаграждение Ozon, Услуги
     доставки, Услуги партнёров, Услуги FBO, Продвижение и реклама,
     Другие услуги и штрафы) that the by-day API's own JSON fields never
     exposed — meaning the cabinet almost certainly generates these exports
     from a DIFFERENT backend report than /v1/finance/accrual/by-day, and
     /v1/finance/realization/by-day is the untried, obviously-named
     candidate for the "Продажи"/"Возвраты" half of that breakdown.

  2. POST /v1/finance/accrual/postings — "начисления по отправлениям"
     (accruals BY POSTING) — a different granularity/shape than
     /v1/finance/accrual/by-day, never tried. Named as a candidate for
     exposing the same "Группа услуг"/"Тип начисления" split per posting
     that the by-day method doesn't.

  3. POST /v1/finance/accrual/types — "типы начислений (справочник)", i.e.
     a REFERENCE/lookup table of accrual type codes — never tried. If
     accrual records carry a type CODE rather than the human-readable
     "Тип начисления" string seen in the XLSX export, this dictionary is
     what would translate one into the other.

NONE of these three methods' request/response contracts are confirmed —
this script tries plausible bodies (informed by the CONFIRMED conventions
of neighboring methods: /v1/finance/accrual/by-day takes `date` as a
plain string + page/page_size; /v1/finance/cash-flow-statement/list takes
`date` as a {from, to} object) and prints whichever succeeds, or Ozon's
own raw error text for each attempt if none do — that error text has
repeatedly named the real required field in this exact project (e.g. the
historical ReviewListRequest.Limit case), so a failure here is still a
useful, real answer, not a dead end.

Never dumps full raw JSON to the console (no scrollback on the target
console) — condensed summary only; full responses saved to --out-dir.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/probe_realization_by_day_and_accrual_reference.py \\
        --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf --date 2026-09-12
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
    """Generic "find the items array" walker — the real top-level key is
    not confirmed for any of these three methods, so this doesn't
    hardcode one."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return ".".join(path) or "(корень)", obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            found_path, found_list = _find_item_list(value, path + (key,))
            if found_list is not None:
                return found_path, found_list
    return None, None


def _print_condensed(label: str, data: dict, out_file: Path) -> None:
    out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    Полный сырой ответ сохранён в {out_file}")

    top_level_preview = {
        k: (f"<{type(v).__name__}, len={len(v)}>" if isinstance(v, (list, dict)) else v) for k, v in data.items()
    }
    print(f"    Верхнеуровневые поля {label}: {json.dumps(top_level_preview, ensure_ascii=False)}")

    item_path, items = _find_item_list(data)
    if items is None:
        print("    Список позиций не найден нигде во вложенной структуре.")
        return
    print(f"    Список позиций найден по пути: {item_path}  (позиций: {len(items)})")
    print("    ПЕРВАЯ позиция целиком:")
    print(json.dumps(items[0], ensure_ascii=False, indent=2))

    # All top-level keys seen across every item — since the real field
    # names aren't confirmed, this at least shows what's available without
    # printing every single item.
    all_keys: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            all_keys.update(item.keys())
    print(f"    Все ключи, встреченные хоть в одной позиции: {sorted(all_keys)}")


def _try_bodies(client, path: str, label: str, bodies: list[tuple[str, dict]], out_file: Path) -> bool:
    print("=" * 70)
    print(f"{label} ({path})")
    print("=" * 70)
    for desc, body in bodies:
        print(f"    Пробую вариант «{desc}»: {json.dumps(body, ensure_ascii=False)}")
        try:
            data = client.probe_finance_endpoint(path, body)
        except OzonAPIError as exc:
            print(f"    {type(exc).__name__}: {exc}")
            continue
        print(f"    УСПЕХ с вариантом «{desc}»")
        _print_condensed(path, data, out_file)
        return True
    print("    Ни один вариант тела запроса не сработал.")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--store-name", default=None)
    parser.add_argument("--date", required=True, help="ГГГГ-ММ-ДД")
    parser.add_argument("--out-dir", default="/tmp", help="куда сохранить полные сырые JSON-файлы")
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
            _try_bodies(
                client, "/v1/finance/realization/by-day", "1) Реализация по дням",
                bodies=[
                    ("date как строка + пагинация (по образцу accrual/by-day)", {"date": args.date, "page": 1, "page_size": 1000}),
                    ("date как диапазон (по образцу ДДС)", {"date": {"from": args.date, "to": args.date}, "page": 1, "page_size": 1000}),
                    ("только дата, без пагинации", {"date": args.date}),
                ],
                out_file=out_dir / f"realization_by_day_{args.date}.json",
            )
            print()
            _try_bodies(
                client, "/v1/finance/accrual/postings", "2) Начисления по отправлениям",
                bodies=[
                    ("date как строка + пагинация (по образцу accrual/by-day)", {"date": args.date, "page": 1, "page_size": 1000}),
                    ("date как диапазон (по образцу ДДС)", {"date": {"from": args.date, "to": args.date}, "page": 1, "page_size": 1000}),
                    ("пустое тело", {}),
                ],
                out_file=out_dir / f"accrual_postings_{args.date}.json",
            )
            print()
            _try_bodies(
                client, "/v1/finance/accrual/types", "3) Справочник типов начислений",
                bodies=[
                    ("пустое тело", {}),
                    ("с языком", {"language": "RU"}),
                ],
                out_file=out_dir / "accrual_types.json",
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
