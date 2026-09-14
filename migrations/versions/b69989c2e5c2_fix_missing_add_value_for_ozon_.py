"""fix missing ADD VALUE for OZON_REALIZATION_REPORT_API and OZON_ACCRUAL_DAILY_API

Revision ID: b69989c2e5c2
Revises: 4e0b54059fd2
Create Date: 2026-09-14 03:59:08.381899

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b69989c2e5c2'
down_revision: Union[str, None] = '4e0b54059fd2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # BUG FIX 2026-09-14: both these SyncSourceType members were added to
    # the Python enum (backend/app/models/sync_run.py) in earlier migrations
    # (ce0c75985ad9 for realization-report, 6de5f9edc39a for accrual-daily)
    # but neither migration added the matching Postgres ALTER TYPE — a real
    # production 500 confirmed this ("invalid input value for enum
    # sync_source_type: OZON_ACCRUAL_DAILY_API") the first time a user
    # actually clicked the accrual-daily sync button; the realization-report
    # one has the identical bug, just not yet exercised for real. ENUM type
    # needs its new value added explicitly — autogenerate does not detect
    # it. Postgres enum label is the Python Enum MEMBER NAME (not its
    # .value), matching every other ADD VALUE migration in this project.
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_REALIZATION_REPORT_API'")
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_ACCRUAL_DAILY_API'")


def downgrade() -> None:
    pass
    # Postgres has no ALTER TYPE ... DROP VALUE — the enum values from
    # upgrade() intentionally stay behind on downgrade, same tradeoff every
    # other ADD VALUE migration in this project already accepts.
