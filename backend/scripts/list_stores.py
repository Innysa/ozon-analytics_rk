"""Diagnostic: lists every store with its id and name — no Ozon API call,
just reads the database. Used to confirm which store_id a diagnostic
script's hardcoded UUID actually points to, after a real mismatch was
found between a per-store dashboard figure and a diagnostic dump that
assumed the wrong store id for "Комфорт дом" (2026-09-19).

Usage (on the real server):

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
    finally:
        db.close()

    for s in stores:
        print(f"{s.id}  —  {s.name}")


if __name__ == "__main__":
    main()
