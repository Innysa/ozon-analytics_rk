"""Diagnostic: finds which Ozon Seller API method returns the ITEM-LEVEL
composition (SKU + quantity) of a supply order ("заявка на поставку") —
POST /v3/supply-order/list (CONFIRMED 2026-10-02, see probe_stocks.py)
only returns order IDs, not contents. This probe first calls that
confirmed-working list method (state=1, the value that worked live) to get
a real order_id, then tries several candidate detail endpoints cataloged
in docs/ozon-seller-api-methods.md under «Поставки (supply-order, draft)»
— contracts for all of them are UNCONFIRMED, this is the first live check.

Goal: a new «В поставке (не отгружено)» column on «Остатки» — per the
user's real cabinet screenshot (02.10.2026), the supply-order detail page
is titled «Состав заявки» and shows SKU/Артикул + Количество per line —
whichever method below reproduces that is the one to build on.

Usage (inside the running container):

    docker compose exec app python backend/scripts/probe_supply_order_details.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.encryption import decrypt_secret  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.ozon_credentials import OzonCredentials  # noqa: E402
from app.models.store import Store  # noqa: E402
from app.services.ozon.client import OzonAPIError, OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402


def _resolve_store_id(db, *, store_id: str | None) -> str | None:
    if store_id:
        return store_id
    all_stores = db.query(Store).all()
    if len(all_stores) == 1:
        print(f"В базе всего один магазин: {all_stores[0].id} — {all_stores[0].name}")
        return all_stores[0].id
    print("Не удалось определить магазин автоматически. Доступные магазины (скопируйте id для --store-id):")
    for s in all_stores:
        print(f"    {s.id}  —  {s.name}")
    return None


def _try_endpoint(client: OzonSellerClient, *, path: str, bodies: list[dict]) -> dict | None:
    for body in bodies:
        try:
            print(f"--- пробую {path} с телом: {json.dumps(body, ensure_ascii=False)}")
            data = client._post(path, body)  # noqa: SLF001 — deliberate raw probe
            print("    успех!")
            return data
        except OzonAPIError as exc:
            print(f"    ошибка: {exc}")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        store_id = _resolve_store_id(db, store_id=args.store_id)
        if not store_id:
            return

        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == store_id).first()
        if not creds:
            print("Для этого магазина не заданы ключи Ozon.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            print("\n========== /v3/supply-order/list (получаем реальный order_id) ==========")
            list_data = client._post(  # noqa: SLF001
                "/v3/supply-order/list",
                {"filter": {"states": [1]}, "limit": 5, "sort_by": 1},
            )
            order_ids = list_data.get("order_ids") or []
            print(f"order_ids: {order_ids}")
            if not order_ids:
                print("Нет ни одной поставки в статусе state=1 — нечего проверять.")
                return
            order_id = order_ids[0]
            print(f"\nБерём order_id={order_id} для проверки деталей\n")

            print("\n========== /v3/supply-order/get ==========")
            data = _try_endpoint(client, path="/v3/supply-order/get", bodies=[
                {"order_ids": [order_id]},
                {"supply_order_id": order_id},
            ])
            if data:
                print(json.dumps(data, ensure_ascii=False, indent=2)[:6000])

            print("\n========== /v1/supply-order/bundle ==========")
            data = _try_endpoint(client, path="/v1/supply-order/bundle", bodies=[
                {"order_ids": [order_id]},
                {"supply_order_id": order_id},
                {"bundle_ids": [order_id]},
            ])
            if data:
                print(json.dumps(data, ensure_ascii=False, indent=2)[:6000])

            print("\n========== /v1/supply-order/details ==========")
            data = _try_endpoint(client, path="/v1/supply-order/details", bodies=[
                {"order_ids": [order_id]},
                {"supply_order_id": order_id},
            ])
            if data:
                print(json.dumps(data, ensure_ascii=False, indent=2)[:6000])
    finally:
        db.close()


if __name__ == "__main__":
    main()
