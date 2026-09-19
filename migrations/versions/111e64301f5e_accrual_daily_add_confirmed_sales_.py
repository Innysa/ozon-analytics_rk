"""accrual daily: add confirmed sales_rub/returns_rub columns

Revision ID: 111e64301f5e
Revises: b69989c2e5c2
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '111e64301f5e'
down_revision: Union[str, None] = 'b69989c2e5c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'accrual_daily_statistics',
        sa.Column('sales_rub', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'),
    )
    op.add_column(
        'accrual_daily_statistics',
        sa.Column('returns_rub', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'),
    )
    op.add_column(
        'accrual_daily_statistics',
        sa.Column('revenue_confirmed', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('accrual_daily_statistics', 'sales_rub', server_default=None)
    op.alter_column('accrual_daily_statistics', 'returns_rub', server_default=None)
    op.alter_column('accrual_daily_statistics', 'revenue_confirmed', server_default=None)


def downgrade() -> None:
    op.drop_column('accrual_daily_statistics', 'revenue_confirmed')
    op.drop_column('accrual_daily_statistics', 'returns_rub')
    op.drop_column('accrual_daily_statistics', 'sales_rub')
