"""product_monthly_plans: plan_ad_budget_rub -> plan_ad_budget_pct (confirmed 2026-09-11 — ad budget is planned as a target ДРР %, not a ruble amount; ruble figure is now always derived at read time)

Revision ID: f4b8d1c6a9e3
Revises: e2a7c4f8b3d1
Create Date: 2026-09-11 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f4b8d1c6a9e3'
down_revision: Union[str, None] = 'e2a7c4f8b3d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('product_monthly_plans', sa.Column('plan_ad_budget_pct', sa.Numeric(precision=6, scale=2), nullable=True))
    # Preserve any already-entered plans: convert the old ruble target into
    # an equivalent % of that same row's planned order revenue, wherever
    # both are known (can't convert a ruble target with no revenue plan to
    # compare it against).
    op.execute(
        """
        UPDATE product_monthly_plans
        SET plan_ad_budget_pct = ROUND(plan_ad_budget_rub / plan_orders_sum_rub * 100, 2)
        WHERE plan_ad_budget_rub IS NOT NULL
          AND plan_orders_sum_rub IS NOT NULL
          AND plan_orders_sum_rub != 0
        """
    )
    op.drop_column('product_monthly_plans', 'plan_ad_budget_rub')


def downgrade() -> None:
    op.add_column('product_monthly_plans', sa.Column('plan_ad_budget_rub', sa.Numeric(precision=14, scale=2), nullable=True))
    op.execute(
        """
        UPDATE product_monthly_plans
        SET plan_ad_budget_rub = ROUND(plan_ad_budget_pct / 100 * plan_orders_sum_rub, 2)
        WHERE plan_ad_budget_pct IS NOT NULL
          AND plan_orders_sum_rub IS NOT NULL
        """
    )
    op.drop_column('product_monthly_plans', 'plan_ad_budget_pct')
