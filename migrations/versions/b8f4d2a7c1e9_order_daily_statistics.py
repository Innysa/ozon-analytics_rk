"""order daily statistics (FBO/FBS postings via Ozon Seller API)

Revision ID: b8f4d2a7c1e9
Revises: a3e6c9f1d7b4
Create Date: 2026-09-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b8f4d2a7c1e9'
down_revision: Union[str, None] = 'a3e6c9f1d7b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same reason as previous new-sync-source migrations: a native Postgres
    # ENUM type needs its new value added explicitly — autogenerate does not
    # detect it.
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_ORDERS_API'")

    op.create_table(
        'order_daily_statistics',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('store_id', sa.String(length=36), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('delivery_schema', sa.String(length=10), nullable=False),
        sa.Column('ordered_units', sa.Integer(), nullable=False),
        sa.Column('ordered_sum_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('ordered_sum_discounted_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('delivered_units', sa.Integer(), nullable=False),
        sa.Column('delivered_sum_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('cost_of_delivered_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('cost_of_delivered_known_units', sa.Integer(), nullable=False),
        sa.Column('cancelled_units', sa.Integer(), nullable=False),
        sa.Column('cancelled_sum_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('unfinished_units', sa.Integer(), nullable=False),
        sa.Column('commission_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('raw_payload', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('store_id', 'date', 'delivery_schema', name='uq_order_daily_stat_store_date_schema'),
    )
    op.create_index(op.f('ix_order_daily_statistics_store_id'), 'order_daily_statistics', ['store_id'])
    op.create_index(op.f('ix_order_daily_statistics_date'), 'order_daily_statistics', ['date'])


def downgrade() -> None:
    op.drop_index(op.f('ix_order_daily_statistics_date'), table_name='order_daily_statistics')
    op.drop_index(op.f('ix_order_daily_statistics_store_id'), table_name='order_daily_statistics')
    op.drop_table('order_daily_statistics')
    # Postgres has no DROP VALUE for enum types; the added label is left in
    # place on downgrade (harmless — same pattern as previous enum additions).
