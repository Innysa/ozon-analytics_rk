"""product price daily snapshots (daily history of Product.price_rub/old_price_rub/fbo_stock/fbs_stock, captured on each catalog sync — real per-day СПП base instead of a single current snapshot)

Revision ID: c9a3f6e1b5d8
Revises: b7e2c9f1a3d5
Create Date: 2026-09-22 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9a3f6e1b5d8'
down_revision: Union[str, None] = 'b7e2c9f1a3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'product_price_daily_snapshots',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('store_id', sa.String(length=36), nullable=False),
        sa.Column('ozon_sku', sa.String(length=64), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('price_rub', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('old_price_rub', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('fbo_stock', sa.Integer(), nullable=True),
        sa.Column('fbs_stock', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('store_id', 'ozon_sku', 'date', name='uq_product_price_daily_snapshot_store_sku_date'),
    )
    op.create_index(op.f('ix_product_price_daily_snapshots_store_id'), 'product_price_daily_snapshots', ['store_id'])
    op.create_index(op.f('ix_product_price_daily_snapshots_ozon_sku'), 'product_price_daily_snapshots', ['ozon_sku'])
    op.create_index(op.f('ix_product_price_daily_snapshots_date'), 'product_price_daily_snapshots', ['date'])


def downgrade() -> None:
    op.drop_index(op.f('ix_product_price_daily_snapshots_date'), table_name='product_price_daily_snapshots')
    op.drop_index(op.f('ix_product_price_daily_snapshots_ozon_sku'), table_name='product_price_daily_snapshots')
    op.drop_index(op.f('ix_product_price_daily_snapshots_store_id'), table_name='product_price_daily_snapshots')
    op.drop_table('product_price_daily_snapshots')
