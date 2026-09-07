"""advertising daily statistics: add Ozon's own per-row ДРР columns

Revision ID: f2d9a6c3e8b1
Revises: e7c1a4d9f2b6
Create Date: 2026-09-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f2d9a6c3e8b1'
down_revision: Union[str, None] = 'e7c1a4d9f2b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same reporting-run source as orders/revenue_rub (columns already
    # existed but, until this fix, always parsed as None — see
    # advertising_daily_statistic_parser.py's module docstring): Ozon's own
    # row-level ДРР percentages, "ДРР в продвижении, %" and "ДРР (общий), %",
    # confirmed via a real account's raw_payload. Stored as-is, never
    # recomputed — same rule as AdvertisingStatistic.drr_promo_pct_ozon/
    # drr_total_pct_ozon.
    op.add_column('advertising_daily_statistics', sa.Column('drr_promo_pct_ozon', sa.Numeric(precision=9, scale=4), nullable=True))
    op.add_column('advertising_daily_statistics', sa.Column('drr_total_pct_ozon', sa.Numeric(precision=9, scale=4), nullable=True))


def downgrade() -> None:
    op.drop_column('advertising_daily_statistics', 'drr_total_pct_ozon')
    op.drop_column('advertising_daily_statistics', 'drr_promo_pct_ozon')
