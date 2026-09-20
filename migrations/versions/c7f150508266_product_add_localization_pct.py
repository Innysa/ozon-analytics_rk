"""product: add localization_pct/localization_period_end (per-SKU локализация)

Revision ID: c7f150508266
Revises: 1ad01e169852
Create Date: 2026-09-20 00:00:00.000002

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7f150508266'
down_revision: Union[str, None] = '1ad01e169852'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('products', sa.Column('localization_pct', sa.Numeric(precision=5, scale=2), nullable=True))
    op.add_column('products', sa.Column('localization_period_end', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('products', 'localization_period_end')
    op.drop_column('products', 'localization_pct')
