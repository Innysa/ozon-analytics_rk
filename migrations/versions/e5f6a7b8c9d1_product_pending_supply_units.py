"""products: add pending_supply_units + OZON_SUPPLY_ORDER_PENDING_API

Revision ID: e5f6a7b8c9d1
Revises: d4e5f6a7b8c9
Create Date: 2026-10-03 00:00:00.000001

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d1'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('products', sa.Column('pending_supply_units', sa.Integer(), nullable=True))
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_SUPPLY_ORDER_PENDING_API'")


def downgrade() -> None:
    op.drop_column('products', 'pending_supply_units')
