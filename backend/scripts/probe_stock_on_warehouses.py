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
entirely. This script is a first RAW probe (does NOT assume the request/
response contract — several guessed request bodies are tried in order, and
whichever succeeds prints the FULL raw response for inspection) to find out
whether stock_on_warehouses exposes those extra numbers (and matches Ozon's
own per-warehouse "Товар-склад" export row-for-row).

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

    print("\n=== ПОЛНЫЙ СЫРОЙ ОТВЕТ ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))

    if target_skus:
        rows = data.get("result", data).get("rows") if isinstance(data.get("result", data), dict) else data.get("rows")
        if rows:
            print(f"\n=== СТРОКИ ДЛЯ SKU {sorted(target_skus)} ===")
            for row in rows:
                row_sku = row.get("sku") or row.get("item_code")
                try:
                    row_sku_int = int(row_sku)
                except (TypeError, ValueError):
                    row_sku_int = None
                if row_sku_int in target_skus:
                    print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
