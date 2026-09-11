"""Read-only diagnostic: prints every store's internal id (a UUID — what
every other script in this directory means by --store-id) next to its
name, so you don't have to guess it from Ozon's own numeric Client-Id
(which is a DIFFERENT number, not usable as --store-id here).

No arguments, no shell quoting to get right — just run it as-is:

    docker compose exec app python backend/scripts/list_stores.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.store import Store  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        stores = db.query(Store).order_by(Store.name).all()
        if not stores:
            print("Магазинов в базе нет.")
            return
        for s in stores:
            print(f"{s.id}  —  {s.name}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
