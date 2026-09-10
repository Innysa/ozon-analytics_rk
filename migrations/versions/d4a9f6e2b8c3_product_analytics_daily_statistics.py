"""product analytics daily statistics (per-SKU funnel via Ozon Seller API /v1/analytics/data, Premium Plus)

Revision ID: d4a9f6e2b8c3
Revises: c1f8e5b3a9d7
Create Date: 2026-09-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd4a9f6e2b8c3'
down_revision: Union[str, None] = 'c1f8e5b3a9d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same reason as previous new-sync-source migrations: a native Postgres
    # ENUM type needs its new value added explicitly — autogenerate does not
    # detect it.
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_ANALYTICS_DATA_API'")

    op.create_table(
        'product_analytics_daily_statistics',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('store_id', sa.String(length=36), nullable=False),
        sa.Column('ozon_sku', sa.String(length=64), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('revenue_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('ordered_units', sa.Integer(), nullable=False),
        sa.Column('views_pdp', sa.Integer(), nullable=False),
        sa.Column('cart_adds_pdp', sa.Integer(), nullable=False),
        sa.Column('cart_conversion_pdp_pct', sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column('sessions_pdp', sa.Integer(), nullable=False),
        sa.Column('position_category', sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('store_id', 'ozon_sku', 'date', name='uq_product_analytics_daily_stat_store_sku_date'),
    )
    op.create_index(op.f('ix_product_analytics_daily_statistics_store_id'), 'product_analytics_daily_statistics', ['store_id'])
    op.create_index(op.f('ix_product_analytics_daily_statistics_ozon_sku'), 'product_analytics_daily_statistics', ['ozon_sku'])
    op.create_index(op.f('ix_product_analytics_daily_statistics_date'), 'product_analytics_daily_statistics', ['date'])


def downgrade() -> None:
    op.drop_index(op.f('ix_product_analytics_daily_statistics_date'), table_name='product_analytics_daily_statistics')
    op.drop_index(op.f('ix_product_analytics_daily_statistics_ozon_sku'), table_name='product_analytics_daily_statistics')
    op.drop_index(op.f('ix_product_analytics_daily_statistics_store_id'), table_name='product_analytics_daily_statistics')
    op.drop_table('product_analytics_daily_statistics')
    # Postgres has no DROP VALUE for enum types; the added label is left in
    # place on downgrade (harmless — same pattern as previous enum additions).
