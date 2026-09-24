"""products/product_price_daily_snapshots: add marketing_seller_price_rub

Revision ID: a1b2c3d4e5f6
Revises: f3a7b9d2c8e4
Create Date: 2026-09-24 00:00:00.000001

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f3a7b9d2c8e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('products', sa.Column('marketing_seller_price_rub', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column(
        'product_price_daily_snapshots',
        sa.Column('marketing_seller_price_rub', sa.Numeric(precision=12, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('product_price_daily_snapshots', 'marketing_seller_price_rub')
    op.drop_column('products', 'marketing_seller_price_rub')
