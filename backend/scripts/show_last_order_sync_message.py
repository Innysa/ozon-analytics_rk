"""Tiny diagnostic script: prints the error_message (plus status/counts) of
the most recent "Заказы" (ozon_orders_api) SyncRun for one store — the same
row shown in "Журнал синхронизаций" on the «Подключение к Ozon» page, which
isn't clickable in the UI, so its full error_message isn't otherwise
visible. No Ozon API call, no raw postings — just one row read straight
from this app's own database. Short output only.

Usage (inside the running container):

    docker compose exec app python backend/scripts/show_last_order_sync_message.py --store-id <id>

    # show more than just the latest run:
    docker compose exec app python backend/scripts/show_last_order_sync_message.py --store-id <id> --last 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.sync_run import SyncRun, SyncSourceType  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--last", type=int, default=1, help="сколько последних прогонов показать (по умолчанию 1)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        runs = (
            db.query(SyncRun)
            .filter(SyncRun.store_id == args.store_id, SyncRun.source_type == SyncSourceType.OZON_ORDERS_API)
            .order_by(SyncRun.started_at.desc())
            .limit(args.last)
            .all()
        )
        if not runs:
            print("Для этого магазина ещё не было ни одного запуска синхронизации заказов.")
            return

        for run in runs:
            print("-" * 60)
            print(f"Начало: {run.started_at} | Конец: {run.finished_at}")
            print(f"Статус: {run.status.value}")
            print(f"Получено: {run.items_fetched} | Создано: {run.items_created} | Обновлено: {run.items_skipped_duplicate}")
            print(f"Сообщение: {run.error_message or '(пусто)'}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
