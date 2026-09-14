"""Regression test for a real production bug (2026-09-14): a new
SyncSourceType enum member (OZON_ACCRUAL_DAILY_API) was added to the
Python model without a matching Postgres `ALTER TYPE ... ADD VALUE`
migration — the accrual-daily sync button crashed with `psycopg.errors.
InvalidTextRepresentation: invalid input value for enum sync_source_type:
"OZON_ACCRUAL_DAILY_API"` the first time a real user clicked it. The
identical, not-yet-exercised bug was found alongside it for
OZON_REALIZATION_REPORT_API (added in an earlier migration, same mistake,
just never triggered for real yet) — both fixed in migration
b69989c2e5c2.

Why the existing 400+ tests never caught this: every other test's
`db_session` fixture builds its schema via `Base.metadata.create_all()`
(see conftest.py's `_create_schema` fixture) — that generates each
Postgres ENUM type fresh from whatever the CURRENT Python enum class
declares, so it can NEVER be missing a label; it never runs the actual
Alembic migration chain at all. This test instead runs the REAL migration
chain against a disposable database and checks that every Postgres enum
contains every label its Python enum class declares — closing this blind
spot for every current AND future Enum-backed column, not just the one
that actually broke."""
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import Enum, create_engine, text

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
ADMIN_DATABASE_URL = os.environ.get(
    "MIGRATION_CHECK_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
)
THROWAWAY_DB_NAME = "ozon_analytics_migration_enum_check"


def _admin_connect():
    return psycopg.connect(ADMIN_DATABASE_URL, autocommit=True)


@pytest.fixture(scope="module")
def migrated_throwaway_db():
    with _admin_connect() as conn, conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{THROWAWAY_DB_NAME}"')
        cur.execute(f'CREATE DATABASE "{THROWAWAY_DB_NAME}"')

    admin_prefix, _, _ = ADMIN_DATABASE_URL.rpartition("/")
    throwaway_url = f"{admin_prefix}/{THROWAWAY_DB_NAME}".replace("postgresql://", "postgresql+psycopg://")

    # migrations/env.py resolves DATABASE_URL via app.core.config.
    # get_settings() (lru_cache'd) rather than the Alembic Config object —
    # running as a SUBPROCESS avoids fighting that cache and this test
    # process's own already-initialized app engine.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": throwaway_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"

    yield throwaway_url

    with _admin_connect() as conn, conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{THROWAWAY_DB_NAME}"')


def _enum_columns() -> dict[str, type]:
    from app.db.base import Base
    import app.models  # noqa: F401  (populate metadata with every model)

    seen: dict[str, type] = {}
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, Enum) and col.type.enum_class is not None:
                seen[col.type.name] = col.type.enum_class
    return seen


def test_every_python_enum_member_exists_in_the_migrated_database(migrated_throwaway_db):
    """Checks EVERY Enum-backed column the app currently has, not just
    sync_source_type — the same "added a member, forgot ADD VALUE"
    mistake is possible for any of them (StoreRole, ReviewStatus, ...)."""
    engine = create_engine(migrated_throwaway_db, future=True)
    try:
        with engine.connect() as conn:
            for pg_enum_name, enum_class in _enum_columns().items():
                assert re.fullmatch(r"[a-z_][a-z0-9_]*", pg_enum_name), f"unexpected enum type name: {pg_enum_name!r}"
                rows = conn.execute(text(f"SELECT unnest(enum_range(NULL::{pg_enum_name}))::text")).all()
                actual_labels = {r[0] for r in rows}
                expected_labels = {member.name for member in enum_class}
                missing = expected_labels - actual_labels
                assert not missing, (
                    f"Postgres enum '{pg_enum_name}' is missing labels {missing} for Python enum "
                    f"{enum_class.__name__} — add a migration with `ALTER TYPE {pg_enum_name} ADD VALUE "
                    f"IF NOT EXISTS '<NAME>'` for each missing member (see any existing ADD VALUE migration)."
                )
    finally:
        engine.dispose()
