"""advertising daily statistics (Ozon Performance API automatic sync)

Revision ID: b3f7c1a9e5d2
Revises: 9a1f5c2e7b3d
Create Date: 2026-09-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b3f7c1a9e5d2'
down_revision: Union[str, None] = '9a1f5c2e7b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alembic's autogenerate does not detect new values on an existing native
    # Postgres ENUM type — must add it explicitly, or SyncSourceType
    # .OZON_ADVERTISING_STATISTICS_API fails at INSERT time with "invalid
    # input value for enum sync_source_type". SQLAlchemy's Enum() column type
    # stores the Python enum *member name*, matching the label below.
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_ADVERTISING_STATISTICS_API'")

    op.create_table(
        'advertising_daily_statistics',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('store_id', sa.String(length=36), sa.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product_id', sa.String(length=36), sa.ForeignKey('products.id', ondelete='SET NULL'), nullable=True),
        sa.Column('campaign_id', sa.String(length=36), sa.ForeignKey('advertising_campaigns.id', ondelete='SET NULL'), nullable=True),
        sa.Column('ozon_campaign_id', sa.String(length=128), nullable=False),
        sa.Column('ozon_sku', sa.String(length=64), nullable=False),
        sa.Column('product_name', sa.String(length=500), nullable=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('product_price_rub', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('page_type', sa.String(length=100), nullable=True),
        sa.Column('impression_condition', sa.String(length=200), nullable=True),
        sa.Column('impressions', sa.Integer(), nullable=True),
        sa.Column('clicks', sa.Integer(), nullable=True),
        sa.Column('ctr_pct_ozon', sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column('cart_additions', sa.Integer(), nullable=True),
        sa.Column('avg_bid_rub_ozon', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('spend_rub', sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column('orders', sa.Integer(), nullable=True),
        sa.Column('revenue_rub', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('orders_model', sa.Integer(), nullable=True),
        sa.Column('revenue_model_rub', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('raw_payload', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('store_id', 'ozon_campaign_id', 'ozon_sku', 'date', name='uq_ad_daily_stat_store_campaign_sku_date'),
    )
    op.create_index(op.f('ix_advertising_daily_statistics_store_id'), 'advertising_daily_statistics', ['store_id'])
    op.create_index(op.f('ix_advertising_daily_statistics_product_id'), 'advertising_daily_statistics', ['product_id'])
    op.create_index(op.f('ix_advertising_daily_statistics_campaign_id'), 'advertising_daily_statistics', ['campaign_id'])
    op.create_index(op.f('ix_advertising_daily_statistics_ozon_campaign_id'), 'advertising_daily_statistics', ['ozon_campaign_id'])
    op.create_index(op.f('ix_advertising_daily_statistics_ozon_sku'), 'advertising_daily_statistics', ['ozon_sku'])
    op.create_index(op.f('ix_advertising_daily_statistics_date'), 'advertising_daily_statistics', ['date'])


def downgrade() -> None:
    op.drop_table('advertising_daily_statistics')
    # Postgres has no DROP VALUE for enum types; the added label is left in
    # place on downgrade (harmless — same pattern as OZON_PRODUCTS_API).
