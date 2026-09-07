"""advertising AI review (campaign analysis, new sync source)

Revision ID: e7c1a4d9f2b6
Revises: c4a8e2f6b1d3
Create Date: 2026-09-07 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e7c1a4d9f2b6'
down_revision: Union[str, None] = 'c4a8e2f6b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same reason as b3f7c1a9e5d2/c4a8e2f6b1d3: a native Postgres ENUM type
    # needs its new value added explicitly — autogenerate does not detect it.
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_ADVERTISING_AI_REVIEW'")

    op.create_table(
        'advertising_ai_reviews',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('store_id', sa.String(length=36), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('campaigns_analyzed', sa.Integer(), nullable=False),
        sa.Column('overview', sa.Text(), nullable=False),
        sa.Column('insights_json', sa.Text(), nullable=True),
        sa.Column('anomalies_json', sa.Text(), nullable=True),
        sa.Column('recommendations_json', sa.Text(), nullable=True),
        sa.Column('model_used', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('store_id', 'period_start', 'period_end', name='uq_advertising_ai_review_store_period'),
    )
    op.create_index(op.f('ix_advertising_ai_reviews_store_id'), 'advertising_ai_reviews', ['store_id'])
    op.create_index(op.f('ix_advertising_ai_reviews_period_start'), 'advertising_ai_reviews', ['period_start'])
    op.create_index(op.f('ix_advertising_ai_reviews_period_end'), 'advertising_ai_reviews', ['period_end'])


def downgrade() -> None:
    op.drop_index(op.f('ix_advertising_ai_reviews_period_end'), table_name='advertising_ai_reviews')
    op.drop_index(op.f('ix_advertising_ai_reviews_period_start'), table_name='advertising_ai_reviews')
    op.drop_index(op.f('ix_advertising_ai_reviews_store_id'), table_name='advertising_ai_reviews')
    op.drop_table('advertising_ai_reviews')
    # Postgres has no DROP VALUE for enum types; the added label is left in
    # place on downgrade (harmless — same pattern as previous enum additions).
