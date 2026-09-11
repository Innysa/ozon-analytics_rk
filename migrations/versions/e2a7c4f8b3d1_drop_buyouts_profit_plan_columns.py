"""product_monthly_plans: drop plan_buyouts_units/sum_rub, plan_profit_rub (confirmed 2026-09-11 — plan is only ever entered for Заказы/Рекламный бюджет)

Revision ID: e2a7c4f8b3d1
Revises: d5e9b3c7a1f6
Create Date: 2026-09-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e2a7c4f8b3d1'
down_revision: Union[str, None] = 'd5e9b3c7a1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('product_monthly_plans', 'plan_buyouts_units')
    op.drop_column('product_monthly_plans', 'plan_buyouts_sum_rub')
    op.drop_column('product_monthly_plans', 'plan_profit_rub')


def downgrade() -> None:
    op.add_column('product_monthly_plans', sa.Column('plan_profit_rub', sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column('product_monthly_plans', sa.Column('plan_buyouts_sum_rub', sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column('product_monthly_plans', sa.Column('plan_buyouts_units', sa.Integer(), nullable=True))
