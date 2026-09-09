"""product order daily statistics (per-SKU slice of FBO/FBS postings via Ozon Seller API)

Revision ID: c1f8e5b3a9d7
Revises: b8f4d2a7c1e9
Create Date: 2026-09-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c1f8e5b3a9d7'
down_revision: Union[str, None] = 'b8f4d2a7c1e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Populated by the SAME sync (order_daily_sync_service.sync_order_daily_statistics)
    # that already fills order_daily_statistics — no new sync source, no new
    # SyncRun type needed.
    op.create_table(
        'product_order_daily_statistics',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('store_id', sa.String(length=36), nullable=False),
        sa.Column('ozon_sku', sa.String(length=64), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('delivery_schema', sa.String(length=10), nullable=False),
        sa.Column('ordered_units', sa.Integer(), nullable=False),
        sa.Column('ordered_sum_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('ordered_sum_discounted_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('delivered_units', sa.Integer(), nullable=False),
        sa.Column('delivered_sum_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('cancelled_units', sa.Integer(), nullable=False),
        sa.Column('cancelled_sum_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('unfinished_units', sa.Integer(), nullable=False),
        sa.Column('commission_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'store_id', 'ozon_sku', 'date', 'delivery_schema',
            name='uq_product_order_daily_stat_store_sku_date_schema',
        ),
    )
    op.create_index(op.f('ix_product_order_daily_statistics_store_id'), 'product_order_daily_statistics', ['store_id'])
    op.create_index(op.f('ix_product_order_daily_statistics_ozon_sku'), 'product_order_daily_statistics', ['ozon_sku'])
    op.create_index(op.f('ix_product_order_daily_statistics_date'), 'product_order_daily_statistics', ['date'])


def downgrade() -> None:
    op.drop_index(op.f('ix_product_order_daily_statistics_date'), table_name='product_order_daily_statistics')
    op.drop_index(op.f('ix_product_order_daily_statistics_ozon_sku'), table_name='product_order_daily_statistics')
    op.drop_index(op.f('ix_product_order_daily_statistics_store_id'), table_name='product_order_daily_statistics')
    op.drop_table('product_order_daily_statistics')
