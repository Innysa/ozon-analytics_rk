"""product_warehouse_stocks: key by warehouse_name, not warehouse_id (unreliable)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-02 00:00:00.000002

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Real account confirmed (2026-10-02, same day as the previous
    # migration): a full-catalog /v2/analytics/stock_on_warehouses call
    # omits warehouse_id for a chunk of warehouses (only warehouse_name is
    # always present) — the original NOT NULL warehouse_id + unique
    # constraint on it silently dropped almost every row (302 fetched, 0
    # saved on a real run). Table is a replace-each-sync snapshot with
    # nothing meaningful in it yet, so this is a straight ALTER, not a
    # backfill.
    op.drop_constraint('uq_product_warehouse_stock_store_sku_warehouse', 'product_warehouse_stocks', type_='unique')
    op.alter_column('product_warehouse_stocks', 'warehouse_id', nullable=True)
    # Table has nothing meaningful saved yet (see this migration's own
    # comment above) — safe to tighten warehouse_name to NOT NULL without
    # a backfill.
    op.alter_column('product_warehouse_stocks', 'warehouse_name', nullable=False)
    op.create_unique_constraint(
        'uq_product_warehouse_stock_store_sku_warehouse',
        'product_warehouse_stocks',
        ['store_id', 'ozon_sku', 'warehouse_name'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_product_warehouse_stock_store_sku_warehouse', 'product_warehouse_stocks', type_='unique')
    op.alter_column('product_warehouse_stocks', 'warehouse_id', nullable=False)
    op.alter_column('product_warehouse_stocks', 'warehouse_name', nullable=True)
    op.create_unique_constraint(
        'uq_product_warehouse_stock_store_sku_warehouse',
        'product_warehouse_stocks',
        ['store_id', 'ozon_sku', 'warehouse_id'],
    )
