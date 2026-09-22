"""product card AI review (full-card analysis: reviews + ads + order trend)

Revision ID: f3a7b9d2c8e4
Revises: c9a3f6e1b5d8
Create Date: 2026-09-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f3a7b9d2c8e4'
down_revision: Union[str, None] = 'c9a3f6e1b5d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'product_card_ai_reviews',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('store_id', sa.String(length=36), nullable=False),
        sa.Column('product_id', sa.String(length=36), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('overview', sa.Text(), nullable=False),
        sa.Column('trend_observations_json', sa.Text(), nullable=True),
        sa.Column('hypotheses_json', sa.Text(), nullable=True),
        sa.Column('recommendations_json', sa.Text(), nullable=True),
        sa.Column('reviews_considered', sa.Integer(), nullable=False),
        sa.Column('model_used', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('store_id', 'product_id', 'period_start', 'period_end', name='uq_product_card_ai_review_store_product_period'),
    )
    op.create_index(op.f('ix_product_card_ai_reviews_store_id'), 'product_card_ai_reviews', ['store_id'])
    op.create_index(op.f('ix_product_card_ai_reviews_product_id'), 'product_card_ai_reviews', ['product_id'])
    op.create_index(op.f('ix_product_card_ai_reviews_period_start'), 'product_card_ai_reviews', ['period_start'])
    op.create_index(op.f('ix_product_card_ai_reviews_period_end'), 'product_card_ai_reviews', ['period_end'])


def downgrade() -> None:
    op.drop_index(op.f('ix_product_card_ai_reviews_period_end'), table_name='product_card_ai_reviews')
    op.drop_index(op.f('ix_product_card_ai_reviews_period_start'), table_name='product_card_ai_reviews')
    op.drop_index(op.f('ix_product_card_ai_reviews_product_id'), table_name='product_card_ai_reviews')
    op.drop_index(op.f('ix_product_card_ai_reviews_store_id'), table_name='product_card_ai_reviews')
    op.drop_table('product_card_ai_reviews')
