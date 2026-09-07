"""One-off diagnostic script: calls Ozon Seller API's POST
/v1/analytics/product-queries/details directly for one store's real SKUs and
prints the exact request and Ozon's raw JSON response to stdout.

Written to debug the reported symptom: the automatic "Позиции в поиске" sync
(app.services.search_query_details_sync_service.sync_search_query_details)
completes with HTTP 200 on every batch but always reports fetched=created=
updated=0. Unlike the app's own temporary logger.warning() calls added
alongside this script, printing straight to stdout here doesn't depend on
this deployment's logging configuration at all — so this is the most direct
way to see exactly what Ozon returns.

Usage (inside the running container):

    docker compose exec app python backend/scripts/debug_search_query_details.py --store-id <id>

    # override the resolved date range / skus for testing, e.g. a wider or
    # older window to rule out Ozon's own aggregation lag or a too-narrow
    # default lookback:
    docker compose exec app python backend/scripts/debug_search_query_details.py \\
        --store-id <id> --date-from 2026-06-01 --date-to 2026-08-31 --sku 5716615794

    # test one specific product by offer_id (looks up its stored ozon_sku in
    # the DB and prints it for cross-checking against a manually-known-good
    # sku from Ozon's own cabinet export — a mismatch here, not the API
    # request itself, is the single most likely reason a sku that has real
    # data in a manual XLSX export still comes back empty from this app):
    docker compose exec app python backend/scripts/debug_search_query_details.py \\
        --store-id <id> --offer-id "пол/обув/4/чер.дер/2" --date-from 2026-08-09 --date-to 2026-09-05

Prints, per batch: the exact date_from/date_to/skus/limit_by_sku/page_size
sent, then Ozon's complete raw JSON response.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.core.encryption import decrypt_secret  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.ozon_credentials import OzonCredentials  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.services.ozon.client import OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402
from app.services.ozon.exceptions import OzonAPIError  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True, help="ID магазина (см. URL в интерфейсе или таблицу stores)")
    parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД, по умолчанию — как в реальном автосборе")
    parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД, по умолчанию — как в реальном автосборе")
    parser.add_argument("--sku", action="append", default=None, help="Конкретный SKU (можно повторить); по умолчанию — все товары магазина")
    parser.add_argument("--offer-id", default=None, help="Найти товар по offer_id в БД и использовать его сохранённый ozon_sku (для сверки с ручным экспортом)")
    args = parser.parse_args()

    settings = get_settings()
    db = SessionLocal()
    try:
        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == args.store_id).first()
        if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
            print("Для этого магазина не заданы ключи Ozon Seller API.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)
        print(f"Client-Id: {client_id}")

        if args.offer_id:
            product = db.query(Product).filter(Product.store_id == args.store_id, Product.offer_id == args.offer_id).first()
            if not product:
                print(f"В БД этого магазина НЕТ товара с offer_id={args.offer_id!r} — сверить не с чем.")
                return
            print(
                f"Найден в БД по offer_id={args.offer_id!r}: id={product.id}, "
                f"ozon_sku={product.ozon_sku!r}, ozon_product_id={product.ozon_product_id!r}, "
                f"name={product.name!r}, is_archived={product.is_archived} "
                f"-- СВЕРЬТЕ ozon_sku с колонкой SKU в ручном XLSX-экспорте Ozon для этого же товара; "
                f"расхождение здесь и есть причина 0 строк, а не сам API-запрос."
            )
            skus = [product.ozon_sku] if product.ozon_sku and product.ozon_sku != "0" else []
            if not skus:
                print("У найденного товара нет валидного ozon_sku (0/пусто) — синхронизируйте каталог (POST /sync/ozon-products) сначала.")
                return
        elif args.sku:
            skus = args.sku
        else:
            products = (
                db.query(Product)
                .filter(Product.store_id == args.store_id, Product.is_archived.is_(False))
                .all()
            )
            skus = [p.ozon_sku for p in products if p.ozon_sku and p.ozon_sku != "0"]
        if not skus:
            print("Нет товаров с валидным SKU для этого магазина (и --sku/--offer-id не переданы).")
            return
        print(f"SKU ({len(skus)}): {skus}")

        today = datetime.now(timezone.utc).date()
        date_to = date.fromisoformat(args.date_to) if args.date_to else today - timedelta(days=settings.SEARCH_QUERY_STATS_DATA_LAG_DAYS)
        date_from = (
            date.fromisoformat(args.date_from)
            if args.date_from
            else date_to - timedelta(days=settings.SEARCH_QUERY_STATS_DEFAULT_LOOKBACK_DAYS - 1)
        )
        date_from_str = f"{date_from.isoformat()}T00:00:00Z"
        date_to_str = f"{date_to.isoformat()}T23:59:59Z"

        batch_size = settings.SEARCH_QUERY_STATS_SKU_BATCH_SIZE
        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            for i in range(0, len(skus), batch_size):
                batch = skus[i : i + batch_size]
                print(f"\n--- Батч {i // batch_size + 1}: sku={batch} ---")
                print(
                    f"Запрос: date_from={date_from_str} date_to={date_to_str} "
                    f"limit_by_sku={settings.SEARCH_QUERY_STATS_LIMIT_BY_SKU} "
                    f"page_size={settings.SEARCH_QUERY_STATS_PAGE_SIZE}"
                )
                try:
                    data = client.get_product_query_details(
                        date_from=date_from_str,
                        date_to=date_to_str,
                        skus=batch,
                        limit_by_sku=settings.SEARCH_QUERY_STATS_LIMIT_BY_SKU,
                        page_size=settings.SEARCH_QUERY_STATS_PAGE_SIZE,
                    )
                except OzonAPIError as exc:
                    print(f"ОШИБКА: {exc}")
                    continue
                print(f"Ответ Ozon:\n{json.dumps(data, ensure_ascii=False, indent=2)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
