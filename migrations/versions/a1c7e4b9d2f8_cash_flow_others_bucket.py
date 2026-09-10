"""cash flow statement periods: add others_total/others_items_json (Прочие удержания — a details[] bucket earlier rounds never observed)

Revision ID: a1c7e4b9d2f8
Revises: e8b3d5a1f4c9
Create Date: 2026-09-10 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1c7e4b9d2f8'
down_revision: Union[str, None] = 'e8b3d5a1f4c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cash_flow_statement_periods', sa.Column('others_total', sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column('cash_flow_statement_periods', sa.Column('others_items_json', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('cash_flow_statement_periods', 'others_items_json')
    op.drop_column('cash_flow_statement_periods', 'others_total')
