"""One-off diagnostic script: does NOT call Ozon — reads what the app itself
already stored in advertising_daily_statistics.raw_payload for a campaign.

Written to debug the reported symptom: automatically-collected daily
advertising statistics (Ozon Performance API's async statistics-report flow,
see app.services.advertising_daily_sync_service) have real spend/impressions/
clicks, but orders/revenue are always empty (0 / "Нет данных") for every
campaign.

_apply_row() in advertising_daily_sync_service.py sets every column
(including orders/revenue_rub) straight from the parsed CSV row, and the
parser (advertising_daily_statistic_parser.py) stores the ENTIRE raw CSV row
as {header_label: value} JSON in raw_payload for every parsed row — before
any column-name mapping is applied. So if a row exists at all (spend/clicks
did get through), raw_payload already contains Ozon's literal column labels
for that row, with no need to call Ozon again: this script just prints it.

This tells us definitively whether:
  (a) the real CSV header has no "Заказы"/"Выручка" columns at all (this
      report type genuinely doesn't carry orders/revenue), or
  (b) the columns are there but under a label _COLUMN_MAP in
      advertising_daily_statistic_parser.py doesn't recognize (e.g. a
      slightly different label than the one guessed), so they're silently
      dropped during parsing.

Usage (inside the running container):

    docker compose exec app python backend/scripts/inspect_advertising_daily_raw_payload.py --store-id <id>

    # narrow to one campaign by name (substring, case-insensitive) or by its
    # Ozon campaign id, and/or a specific date:
    docker compose exec app python backend/scripts/inspect_advertising_daily_raw_payload.py \\
        --store-id <id> --campaign-name "Целевая аптечки"
    docker compose exec app python backend/scripts/inspect_advertising_daily_raw_payload.py \\
        --store-id <id> --ozon-campaign-id 1234567 --date 2026-09-05

Prints, per matching row: date, SKU, and the full raw_payload JSON exactly as
stored (Ozon's own column labels as keys).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.models.advertising_campaign import AdvertisingCampaign  # noqa: E402
from app.models.advertising_daily_statistic import AdvertisingDailyStatistic  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--campaign-name", default=None, help="Substring match, case-insensitive")
    parser.add_argument("--ozon-campaign-id", default=None)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD, narrows to one day")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        q = db.query(AdvertisingDailyStatistic).filter(AdvertisingDailyStatistic.store_id == args.store_id)
        if args.ozon_campaign_id:
            q = q.filter(AdvertisingDailyStatistic.ozon_campaign_id == args.ozon_campaign_id)
        if args.date:
            q = q.filter(AdvertisingDailyStatistic.date == date.fromisoformat(args.date))
        if args.campaign_name:
            matching_ids = {
                c.ozon_campaign_id
                for c in db.query(AdvertisingCampaign).filter(AdvertisingCampaign.store_id == args.store_id).all()
                if c.name and args.campaign_name.lower() in c.name.lower()
            }
            if not matching_ids:
                print(f"Кампания с именем, содержащим {args.campaign_name!r}, не найдена для этого магазина.")
                return
            q = q.filter(AdvertisingDailyStatistic.ozon_campaign_id.in_(matching_ids))

        rows = q.order_by(AdvertisingDailyStatistic.date.desc()).limit(args.limit).all()
        if not rows:
            print("Нет строк advertising_daily_statistics под эти условия — сначала запустите автосбор.")
            return

        for r in rows:
            print("=" * 70)
            print(f"campaign={r.ozon_campaign_id} sku={r.ozon_sku} date={r.date}")
            print(f"parsed: orders={r.orders!r} revenue_rub={r.revenue_rub!r} spend_rub={r.spend_rub!r} clicks={r.clicks!r}")
            print("raw_payload (Ozon's own column labels for this row):")
            if r.raw_payload:
                print(json.dumps(json.loads(r.raw_payload), ensure_ascii=False, indent=2))
            else:
                print("  (пусто — raw_payload не сохранился для этой строки)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
