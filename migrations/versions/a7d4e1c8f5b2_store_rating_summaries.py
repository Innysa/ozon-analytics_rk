"""store rating summaries (store-wide localization % from Ozon /v1/rating/summary, for «РНП Товары»'s Итого row)

Revision ID: a7d4e1c8f5b2
Revises: f4b8d1c6a9e3
Create Date: 2026-09-11 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a7d4e1c8f5b2'
down_revision: Union[str, None] = 'f4b8d1c6a9e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ENUM type needs its new value added explicitly — autogenerate does not
    # detect it. Postgres enum label is the Python Enum MEMBER NAME (not its
    # .value), matching every other ADD VALUE migration in this project.
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_RATING_SUMMARY_API'")

    op.create_table(
        'store_rating_summaries',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('store_id', sa.String(length=36), nullable=False),
        sa.Column('localization_pct', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('localization_calculation_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_store_rating_summaries_store_id'), 'store_rating_summaries', ['store_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_store_rating_summaries_store_id'), table_name='store_rating_summaries')
    op.drop_table('store_rating_summaries')
    # Postgres has no ALTER TYPE ... DROP VALUE — the enum value from
    # upgrade() intentionally stays behind on downgrade, same tradeoff every
    # other ADD VALUE migration in this project already accepts.
