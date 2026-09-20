"""order daily: add seller-price columns for a correct СПП base

Revision ID: 8fd72062bd21
Revises: 111e64301f5e
Create Date: 2026-09-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8fd72062bd21'
down_revision: Union[str, None] = '111e64301f5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'order_daily_statistics',
        sa.Column('ordered_sum_seller_price_rub', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'),
    )
    op.add_column(
        'order_daily_statistics',
        sa.Column('ordered_sum_discounted_for_known_seller_price_rub', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'),
    )
    op.add_column(
        'order_daily_statistics',
        sa.Column('ordered_units_with_known_seller_price', sa.Integer(), nullable=False, server_default='0'),
    )
    op.alter_column('order_daily_statistics', 'ordered_sum_seller_price_rub', server_default=None)
    op.alter_column('order_daily_statistics', 'ordered_sum_discounted_for_known_seller_price_rub', server_default=None)
    op.alter_column('order_daily_statistics', 'ordered_units_with_known_seller_price', server_default=None)


def downgrade() -> None:
    op.drop_column('order_daily_statistics', 'ordered_units_with_known_seller_price')
    op.drop_column('order_daily_statistics', 'ordered_sum_discounted_for_known_seller_price_rub')
    op.drop_column('order_daily_statistics', 'ordered_sum_seller_price_rub')
