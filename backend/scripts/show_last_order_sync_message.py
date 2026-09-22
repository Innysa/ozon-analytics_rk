"""Tiny diagnostic script: prints the error_message (plus status/counts) of
the most recent "Заказы" (ozon_orders_api) SyncRun for one store — the same
row shown in "Журнал синхронизаций" on the «Подключение к Ozon» page, which
isn't clickable in the UI, so its full error_message isn't otherwise
visible. No Ozon API call, no raw postings — just one row read straight
from this app's own database. Short output only.

Usage (inside the running container):

    docker compose exec app python backend/scripts/show_last_order_sync_message.py --store-id <id>

    # or by name instead of the UUID:
    docker compose exec app python backend/scripts/show_last_order_sync_message.py --store-name "Комфорт дом"

    # show more than just the latest run:
    docker compose exec app python backend/scripts/show_last_order_sync_message.py --store-name "Комфорт дом" --last 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.store import Store  # noqa: E402
from app.models.sync_run import SyncRun, SyncSourceType  # noqa: E402


def _resolve_store_id(db, *, store_id: str | None, store_name: str | None) -> str | None:
    """--store-id is the internal UUID (Store.id) — NOT Ozon's own numeric
    Seller ID. --store-name sidesteps needing that UUID at all: a
    case-insensitive substring match against Store.name."""
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-id", default=None, help="внутренний UUID магазина")
    parser.add_argument("--store-name", default=None, help="имя магазина — альтернатива --store-id")
    parser.add_argument("--last", type=int, default=1, help="сколько последних прогонов показать (по умолчанию 1)")
    args = parser.parse_args()

    if not args.store_id and not args.store_name:
        print("Укажите --store-id или --store-name.")
        return

    db = SessionLocal()
    try:
        store_id = _resolve_store_id(db, store_id=args.store_id, store_name=args.store_name)
        if not store_id:
            return

        runs = (
            db.query(SyncRun)
            .filter(SyncRun.store_id == store_id, SyncRun.source_type == SyncSourceType.OZON_ORDERS_API)
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
