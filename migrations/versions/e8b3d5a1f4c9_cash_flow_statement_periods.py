"""cash flow statement periods (Ozon Seller API /v1/finance/cash-flow-statement/list — logistics/services, replaces obsolete /v3/finance/transaction/list)

Revision ID: e8b3d5a1f4c9
Revises: d4a9f6e2b8c3
Create Date: 2026-09-10 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e8b3d5a1f4c9'
down_revision: Union[str, None] = 'd4a9f6e2b8c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same reason as previous new-sync-source migrations: a native Postgres
    # ENUM type needs its new value added explicitly — autogenerate does not
    # detect it.
    op.execute("ALTER TYPE sync_source_type ADD VALUE IF NOT EXISTS 'OZON_CASH_FLOW_STATEMENT_API'")

    op.create_table(
        'cash_flow_statement_periods',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('store_id', sa.String(length=36), nullable=False),
        sa.Column('period_begin', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('orders_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('returns_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('commission_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('services_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('item_delivery_and_return_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('currency_code', sa.String(length=10), nullable=False),
        sa.Column('begin_balance_amount', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('delivery_total', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('delivery_amount', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('delivery_services_total', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('delivery_services_items_json', sa.Text(), nullable=True),
        sa.Column('delivery_return_total', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('delivery_return_items_json', sa.Text(), nullable=True),
        sa.Column('services_total', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('services_items_json', sa.Text(), nullable=True),
        sa.Column('rfbs_total', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('loan', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('invoice_transfer', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('raw_payload', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('store_id', 'period_begin', 'period_end', name='uq_cash_flow_period_store_range'),
    )
    op.create_index(op.f('ix_cash_flow_statement_periods_store_id'), 'cash_flow_statement_periods', ['store_id'])
    op.create_index(op.f('ix_cash_flow_statement_periods_period_begin'), 'cash_flow_statement_periods', ['period_begin'])
    op.create_index(op.f('ix_cash_flow_statement_periods_period_end'), 'cash_flow_statement_periods', ['period_end'])


def downgrade() -> None:
    op.drop_index(op.f('ix_cash_flow_statement_periods_period_end'), table_name='cash_flow_statement_periods')
    op.drop_index(op.f('ix_cash_flow_statement_periods_period_begin'), table_name='cash_flow_statement_periods')
    op.drop_index(op.f('ix_cash_flow_statement_periods_store_id'), table_name='cash_flow_statement_periods')
    op.drop_table('cash_flow_statement_periods')
    # Postgres has no DROP VALUE for enum types; the added label is left in
    # place on downgrade (harmless — same pattern as previous enum additions).
