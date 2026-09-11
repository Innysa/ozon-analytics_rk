"""product monthly plans (manual per-product monthly targets for the «РНП Товары» planner page)

Revision ID: d5e9b3c7a1f6
Revises: c3f8a2b1e9d4
Create Date: 2026-09-11 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd5e9b3c7a1f6'
down_revision: Union[str, None] = 'c3f8a2b1e9d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'product_monthly_plans',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('store_id', sa.String(length=36), nullable=False),
        sa.Column('product_id', sa.String(length=36), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('plan_orders_units', sa.Integer(), nullable=True),
        sa.Column('plan_orders_sum_rub', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('plan_buyouts_units', sa.Integer(), nullable=True),
        sa.Column('plan_buyouts_sum_rub', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('plan_ad_budget_rub', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('plan_profit_rub', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('store_id', 'product_id', 'year', 'month', name='uq_product_monthly_plan_store_product_period'),
    )
    op.create_index(op.f('ix_product_monthly_plans_store_id'), 'product_monthly_plans', ['store_id'])
    op.create_index(op.f('ix_product_monthly_plans_product_id'), 'product_monthly_plans', ['product_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_product_monthly_plans_product_id'), table_name='product_monthly_plans')
    op.drop_index(op.f('ix_product_monthly_plans_store_id'), table_name='product_monthly_plans')
    op.drop_table('product_monthly_plans')
