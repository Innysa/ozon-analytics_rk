"""accrual report daily statistics (manual XLSX upload of Ozon's own «Начисления» report — exact per-day/group/type sums, replaces guesswork on cash-flow-statement for Логистика и услуги)

Revision ID: a1c4f7d92e56
Revises: c7f150508266
Create Date: 2026-09-22 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1c4f7d92e56'
down_revision: Union[str, None] = 'c7f150508266'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'accrual_report_daily_statistics',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('store_id', sa.String(length=36), nullable=False),
        sa.Column('accrual_date', sa.Date(), nullable=False),
        sa.Column('service_group', sa.String(length=100), nullable=False),
        sa.Column('charge_type', sa.String(length=150), nullable=False),
        sa.Column('amount_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('rows_count', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'store_id', 'accrual_date', 'service_group', 'charge_type',
            name='uq_accrual_report_daily_store_date_group_type',
        ),
    )
    op.create_index(op.f('ix_accrual_report_daily_statistics_store_id'), 'accrual_report_daily_statistics', ['store_id'])
    op.create_index(op.f('ix_accrual_report_daily_statistics_accrual_date'), 'accrual_report_daily_statistics', ['accrual_date'])


def downgrade() -> None:
    op.drop_index(op.f('ix_accrual_report_daily_statistics_accrual_date'), table_name='accrual_report_daily_statistics')
    op.drop_index(op.f('ix_accrual_report_daily_statistics_store_id'), table_name='accrual_report_daily_statistics')
    op.drop_table('accrual_report_daily_statistics')
