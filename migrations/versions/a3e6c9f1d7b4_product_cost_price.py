"""products: add manually-entered cost_price_rub (себестоимость)

Revision ID: a3e6c9f1d7b4
Revises: f2d9a6c3e8b1
Create Date: 2026-09-08 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a3e6c9f1d7b4'
down_revision: Union[str, None] = 'f2d9a6c3e8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ozon has no API that exposes a seller's own purchase/production cost —
    # it's private business data, so this is entered manually per product
    # (once, not a repeated file upload) rather than synced from anywhere.
    op.add_column('products', sa.Column('cost_price_rub', sa.Numeric(precision=12, scale=2), nullable=True))


def downgrade() -> None:
    op.drop_column('products', 'cost_price_rub')
