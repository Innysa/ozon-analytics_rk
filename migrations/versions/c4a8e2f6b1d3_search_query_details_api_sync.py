"""search query details API sync (query_index column, new sync source)

Revision ID: c4a8e2f6b1d3
Revises: b3f7c1a9e5d2
Create Date: 2026-09-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c4a8e2f6b1d3'
down_revision: Union[str, None] = 'b3f7c1a9e5d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same reason as b3f7c1a9e5d2: a native Postgres ENUM type needs its new
    # value added explicitly — autogenerate does not detect it.
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_SEARCH_QUERY_STATISTICS_API'")

    op.add_column(
        'search_query_statistics',
        sa.Column('query_index', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('search_query_statistics', 'query_index')
    # Postgres has no DROP VALUE for enum types; the added label is left in
    # place on downgrade (harmless — same pattern as OZON_ADVERTISING_STATISTICS_API).
