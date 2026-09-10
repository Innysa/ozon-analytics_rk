"""One-off diagnostic script: calls Ozon Seller API's POST /v1/analytics/data
directly for one store and prints Ozon's raw JSON response.

Written to confirm what this app's client.py could not: the REQUEST contract
for this method (dimension/metrics enums, Premium Plus gating) is confirmed
straight from the official docs, but the docs' own example response has an
empty "data": [] — so how a populated row actually represents its dimension
values and metric values is unknown. This script makes exactly ONE real call
(the docs state a 1-request-per-minute limit for this method) and prints the
full response, so that shape can be read off directly instead of guessed.

Usage (inside the running container):

    docker compose exec app python backend/scripts/debug_analytics_data.py --store-id <id>

    # override the date range / metrics for testing:
    docker compose exec app python backend/scripts/debug_analytics_data.py \\
        --store-id <id> --date-from 2026-08-01 --date-to 2026-09-01 --limit 20

Defaults to dimension=[sku, day] and a representative set of Premium
Plus-only funnel metrics (hits_view_pdp, hits_tocart_pdp, conv_tocart_pdp,
session_view_pdp, position_category) plus the two free ones (revenue,
ordered_units) — exactly the metrics the product detail page's "Продажи"
funnel would need, so a real response here tells us directly whether this
API can replace the manual "Аналитика → Товары" CSV upload.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.encryption import decrypt_secret  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.ozon_credentials import OzonCredentials  # noqa: E402
from app.services.ozon.client import OzonCredentials as ClientCredentials  # noqa: E402
from app.services.ozon.client import OzonSellerClient  # noqa: E402
from app.services.ozon.exceptions import OzonAPIError  # noqa: E402

_DEFAULT_METRICS = [
    "revenue", "ordered_units",
    "hits_view_pdp", "hits_tocart_pdp", "conv_tocart_pdp", "session_view_pdp", "position_category",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД, по умолчанию — 7 дней назад")
    parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД, по умолчанию — сегодня")
    parser.add_argument("--limit", type=int, default=20, help="Макс. строк в ответе (1-1000), по умолчанию 20")
    parser.add_argument(
        "--metrics", nargs="+", default=_DEFAULT_METRICS,
        help=f"Список метрик (макс. 14), по умолчанию: {_DEFAULT_METRICS}",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == args.store_id).first()
        if not creds or not creds.client_id_encrypted or not creds.api_key_encrypted:
            print("Для этого магазина не заданы ключи Ozon Seller API.")
            return
        client_id = decrypt_secret(creds.client_id_encrypted)
        api_key = decrypt_secret(creds.api_key_encrypted)

        today = date.today()
        date_to = date.fromisoformat(args.date_to) if args.date_to else today
        date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=6)

        print(f"date_from={date_from.isoformat()} date_to={date_to.isoformat()}")
        print(f"dimension=['sku', 'day'] metrics={args.metrics} limit={args.limit}")
        print("(метод ограничен 1 запросом в минуту — сделаю ровно один вызов)")
        print()

        with OzonSellerClient(ClientCredentials(client_id=client_id, api_key=api_key)) as client:
            response = client.get_analytics_data(
                date_from=date_from.isoformat(),
                date_to=date_to.isoformat(),
                dimension=["sku", "day"],
                metrics=args.metrics,
                limit=args.limit,
            )

        print("=== Полный сырой ответ Ozon ===")
        print(json.dumps(response, ensure_ascii=False, indent=2))
    except OzonAPIError as exc:
        print(f"Ozon вернул ошибку: {exc}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
