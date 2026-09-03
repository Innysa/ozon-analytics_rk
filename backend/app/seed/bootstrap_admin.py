"""One-off CLI to create the first platform admin, so a fresh deployment isn't
locked out (no signup flow exists by design — admins provision users).

Usage: python -m app.seed.bootstrap_admin admin@example.com "Full Name" password
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import User


def main() -> None:
    if len(sys.argv) != 4:
        print("Usage: python -m app.seed.bootstrap_admin <email> <full_name> <password>")
        raise SystemExit(1)

    email, full_name, password = sys.argv[1].lower(), sys.argv[2], sys.argv[3]
    db = SessionLocal()
    try:
        existing = db.scalars(select(User).where(User.email == email)).first()
        if existing:
            print(f"Пользователь {email} уже существует (id={existing.id})")
            return
        user = User(email=email, full_name=full_name, password_hash=hash_password(password), is_admin=True)
        db.add(user)
        db.commit()
        print(f"Создан администратор {email} (id={user.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
