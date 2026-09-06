"""product inventory sync (stocks/price from Ozon product API)

Revision ID: 9a1f5c2e7b3d
Revises: 580d61c0cc99
Create Date: 2026-09-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '9a1f5c2e7b3d'
down_revision: Union[str, None] = '580d61c0cc99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alembic's autogenerate does not detect new values on an existing native
    # Postgres ENUM type — must add it explicitly, or SyncSourceType.OZON_PRODUCTS_API
    # fails at INSERT time with "invalid input value for enum sync_source_type".
    # SQLAlchemy's Enum() column type stores the Python enum *member name*
    # (e.g. "OZON_API"), not its .value, matching the existing labels below.
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_PRODUCTS_API'")
    op.add_column('products', sa.Column('ozon_product_id', sa.BigInteger(), nullable=True))
    op.add_column('products', sa.Column('price_rub', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column('products', sa.Column('old_price_rub', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column('products', sa.Column('fbo_stock', sa.Integer(), nullable=True))
    op.add_column('products', sa.Column('fbs_stock', sa.Integer(), nullable=True))
    op.add_column('products', sa.Column('is_archived', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('products', 'is_archived', server_default=None)


def downgrade() -> None:
    op.drop_column('products', 'is_archived')
    op.drop_column('products', 'fbs_stock')
    op.drop_column('products', 'fbo_stock')
    op.drop_column('products', 'old_price_rub')
    op.drop_column('products', 'price_rub')
    op.drop_column('products', 'ozon_product_id')
    # Postgres has no DROP VALUE for enum types; the added label is left in
    # place on downgrade (harmless — same pattern as OZON_ADVERTISING_API).
