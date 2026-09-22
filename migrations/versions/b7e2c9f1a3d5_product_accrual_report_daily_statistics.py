"""product accrual report daily statistics (per-SKU slice of the manual «Начисления» XLSX upload — logistics/storage/acquiring costs per product, for «РНП Товары»)

Revision ID: b7e2c9f1a3d5
Revises: a1c4f7d92e56
Create Date: 2026-09-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7e2c9f1a3d5'
down_revision: Union[str, None] = 'a1c4f7d92e56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'product_accrual_report_daily_statistics',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('store_id', sa.String(length=36), nullable=False),
        sa.Column('ozon_sku', sa.String(length=64), nullable=False),
        sa.Column('accrual_date', sa.Date(), nullable=False),
        sa.Column('service_group', sa.String(length=100), nullable=False),
        sa.Column('charge_type', sa.String(length=150), nullable=False),
        sa.Column('amount_rub', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('rows_count', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'store_id', 'ozon_sku', 'accrual_date', 'service_group', 'charge_type',
            name='uq_product_accrual_report_daily_store_sku_date_group_type',
        ),
    )
    op.create_index(op.f('ix_product_accrual_report_daily_statistics_store_id'), 'product_accrual_report_daily_statistics', ['store_id'])
    op.create_index(op.f('ix_product_accrual_report_daily_statistics_ozon_sku'), 'product_accrual_report_daily_statistics', ['ozon_sku'])
    op.create_index(op.f('ix_product_accrual_report_daily_statistics_accrual_date'), 'product_accrual_report_daily_statistics', ['accrual_date'])


def downgrade() -> None:
    op.drop_index(op.f('ix_product_accrual_report_daily_statistics_accrual_date'), table_name='product_accrual_report_daily_statistics')
    op.drop_index(op.f('ix_product_accrual_report_daily_statistics_ozon_sku'), table_name='product_accrual_report_daily_statistics')
    op.drop_index(op.f('ix_product_accrual_report_daily_statistics_store_id'), table_name='product_accrual_report_daily_statistics')
    op.drop_table('product_accrual_report_daily_statistics')
