"""review_ai_analysis: add infographic_ideas_json (previously "Идеи для инфографики" on the Дашборд just duplicated card_improvements — no distinct AI field existed)

Revision ID: c3f8a2b1e9d4
Revises: a1c7e4b9d2f8
Create Date: 2026-09-10 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3f8a2b1e9d4'
down_revision: Union[str, None] = 'a1c7e4b9d2f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('review_ai_analysis', sa.Column('infographic_ideas_json', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('review_ai_analysis', 'infographic_ideas_json')
