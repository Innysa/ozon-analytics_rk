"""Diagnostic: raw probe of POST /v2/analytics/stock_on_warehouses — UNVERIFIED
contract (see docs/ozon-seller-api-methods.md's own "Аналитика (analytics)"
section, noted as "возможный кандидат на более точные Остатки товаров" but
never actually called before this).

Built 2026-09-20 after confirming (via backend/scripts/probe_product_stock_
detail.py on two real SKUs) that our current fbo_stock field — sum of
`stocks.stocks[].present` from /v3/product/info/list — closely matches Ozon's
own "Доступно к продаже" figure, NOT the "Всего товаров" headline number
shown on Ozon's «Управление остатками» page (real example: SKU 2953864771
had present=797/reserved=9 from product/info/list, while the SAME SKU's own
"Управление остатками" export showed Всего товаров=896, Доступно=796 — так
present ≈ доступно, and the extra 99 units product/info/list simply does not
expose ANY field for at all — no "недоступно"/"в пути"/"возврат" breakdown
exists in that endpoint's response).

The «Управление остатками» page must be pulling from a different report
entirely.

CONFIRMED 2026-09-20 (real account, first candidate body worked): the
response IS per (sku, warehouse_name) rows with `free_to_sell_amount`,
`reserved_amount`, `promised_amount` — and, checked against the SAME two
real SKUs' own "Товар-склад" export rows, `free_to_sell_amount` matches
"Доступно к продаже" almost exactly (e.g. 60==60 for one warehouse row),
and `free_to_sell_amount + reserved_amount + promised_amount` matches
"Всего товаров" almost exactly (e.g. 60+1+35=96==96 for that same row).
Small per-row mismatches elsewhere are consistent with ordinary stock
drift between when the export was taken and when this probe ran (a live
store's stock changes continuously), not a field-mapping error — TWO
independent rows matched exactly. This script prints per-SKU TOTALS
(summed across every warehouse row) LAST rather than the full raw
response, since the VNC console this project is normally driven through
has no scrollback — only what's printed last is guaranteed visible.

Usage (on the real server, against the real database):

    docker compose exec app python backend/scripts/probe_stock_on_warehouses.py \\
        --store-id a586ccc5-6030-4ec9-b133-da9de24dafcf \\
        --sku 2953864771 --sku 3034472572
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
from app.services.ozon.client import OzonAPIError, OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402

# Candidate request bodies, tried in order until one doesn't raise. Ozon's
# 400 error body (surfaced verbatim by OzonSellerClient._post) usually names
# the exact field it expected, so even every candidate failing is useful —
# the LAST error seen is printed in full.
_CANDIDATE_BODIES = [
    {"limit": 1000, "offset": 0, "warehouse_type": "ALL"},
    {"limit": 1000, "offset": 0},
    {},
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--sku", action="append", default=[], help="ozon_sku для фильтрации вывода (можно несколько раз)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == args.store_id).first()
        if not creds:
            print("Для этого магазина не заданы ключи Ozon.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)

        target_skus = {int(s) for s in args.sku}

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            data = None
            last_error: Exception | None = None
            for body in _CANDIDATE_BODIES:
                try:
                    print(f"--- пробую тело запроса: {json.dumps(body, ensure_ascii=False)}")
                    data = client._post("/v2/analytics/stock_on_warehouses", body)  # noqa: SLF001 — deliberate raw probe
                    print("    успех!")
                    break
                except OzonAPIError as exc:
                    print(f"    ошибка: {exc}")
                    last_error = exc
            if data is None:
                print("\nНи одно из тестовых тел запроса не сработало. Последняя ошибка Ozon (см. выше) обычно называет,")
                print("какое поле ожидалось — используйте её, чтобы поправить _CANDIDATE_BODIES и повторить.")
                if last_error:
                    raise last_error
                return
    finally:
        db.close()

    result = data.get("result", data) if isinstance(data, dict) else data
    rows = result.get("rows") if isinstance(result, dict) else None
    rows = rows or []

    # Печатаем ИТОГИ последними (не полный сырой ответ) — в VNC-консоли без
    # прокрутки видно только то, что напечатано последним, а сырой ответ на
    # ~40 складов/SKU легко не помещается на экран целиком.
    print(f"\n=== МЕТА: строк всего={len(rows)}", end="")
    for meta_key in ("total", "count", "has_next"):
        if isinstance(result, dict) and meta_key in result:
            print(f", {meta_key}={result[meta_key]}", end="")
    print(" ===")
    print("(если строк ровно 1000 — вероятно, обрезано лимитом запроса, часть складов не попала)")

    if target_skus:
        totals: dict[int, dict[str, float]] = {sku: {"free_to_sell": 0.0, "reserved": 0.0, "promised": 0.0, "rows": 0} for sku in target_skus}
        matched_rows: dict[int, list[dict]] = {sku: [] for sku in target_skus}
        for row in rows:
            row_sku = row.get("sku") or row.get("item_code")
            try:
                row_sku_int = int(row_sku)
            except (TypeError, ValueError):
                continue
            if row_sku_int not in target_skus:
                continue
            t = totals[row_sku_int]
            t["free_to_sell"] += float(row.get("free_to_sell_amount") or 0)
            t["reserved"] += float(row.get("reserved_amount") or 0)
            t["promised"] += float(row.get("promised_amount") or 0)
            t["rows"] += 1
            matched_rows[row_sku_int].append(row)

        # CONFIRMED 2026-09-20: per-SKU row COUNTS came back far lower than
        # this account's real non-zero-warehouse count (e.g. SKU 3034472572
        # got only 4 rows here vs 17 non-zero warehouses in the "Товар-склад"
        # export summing to the same SKU's own confirmed 99 "Всего товаров"
        # — a ~57-unit gap far too large to be ordinary stock drift). So the
        # per-SKU warehouse LIST itself, not just the totals, needs checking
        # — printed here (still before the totals, which stay last/visible).
        for sku, sku_rows in matched_rows.items():
            print(f"\n--- строки для SKU {sku} (склад: free_to_sell/reserved/promised) ---")
            for row in sku_rows:
                print(
                    f"  {row.get('warehouse_name')}: "
                    f"{row.get('free_to_sell_amount')}/{row.get('reserved_amount')}/{row.get('promised_amount')}"
                )

        print(f"\n=== ИТОГО ПО SKU (это последнее, что напечатано — должно быть видно без прокрутки) ===")
        for sku, t in totals.items():
            total_all = t["free_to_sell"] + t["reserved"] + t["promised"]
            print(
                f"SKU {sku}: строк-складов={int(t['rows'])}  "
                f"free_to_sell(≈Доступно)={t['free_to_sell']:.0f}  "
                f"reserved={t['reserved']:.0f}  promised={t['promised']:.0f}  "
                f"СУММА ВСЕХ ТРЁХ (≈Всего товаров)={total_all:.0f}"
            )


if __name__ == "__main__":
    main()
