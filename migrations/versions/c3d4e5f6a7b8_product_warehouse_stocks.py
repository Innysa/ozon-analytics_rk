"""product_warehouse_stocks table + OZON_STOCK_ON_WAREHOUSES_API sync source

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-10-02 00:00:00.000001

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'product_warehouse_stocks',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('store_id', sa.String(length=36), sa.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ozon_sku', sa.String(length=64), nullable=False),
        sa.Column('offer_id', sa.String(length=255), nullable=True),
        sa.Column('warehouse_id', sa.BigInteger(), nullable=False),
        sa.Column('warehouse_name', sa.String(length=255), nullable=True),
        sa.Column('cluster_id', sa.Integer(), nullable=True),
        sa.Column('cluster_name', sa.String(length=255), nullable=True),
        sa.Column('free_to_sell_amount', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('reserved_amount', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('promised_amount', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('store_id', 'ozon_sku', 'warehouse_id', name='uq_product_warehouse_stock_store_sku_warehouse'),
    )
    op.create_index('ix_product_warehouse_stocks_store_id', 'product_warehouse_stocks', ['store_id'])
    op.create_index('ix_product_warehouse_stocks_ozon_sku', 'product_warehouse_stocks', ['ozon_sku'])

    # Postgres enum label is the Python Enum MEMBER NAME, not its .value —
    # see b69989c2e5c2's own comment for why this must be a separate
    # explicit ALTER TYPE (autogenerate never detects it).
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_STOCK_ON_WAREHOUSES_API'")


def downgrade() -> None:
    op.drop_index('ix_product_warehouse_stocks_ozon_sku', table_name='product_warehouse_stocks')
    op.drop_index('ix_product_warehouse_stocks_store_id', table_name='product_warehouse_stocks')
    op.drop_table('product_warehouse_stocks')
    # Postgres has no ALTER TYPE ... DROP VALUE — same tradeoff every other
    # ADD VALUE migration in this project already accepts.
