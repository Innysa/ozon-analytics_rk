"""Per-SKU slice of AccrualReportDailyStatistic — same manual «Начисления»
XLSX upload (see that model's/app.services.accrual_report_import's own
docstrings for the full story: real per-day dates, Ozon's own «Группа
услуг»/«Тип начисления» naming, no confirmed API equivalent), just kept per
`SKU` (a real column on every row of the confirmed real export) instead of
summed away across the whole store.

Requested by the user (2026-09-22) after she showed a competing paid tool
(«Система 10X» / mp.btlz-api.ru) displaying logistics/storage/acquiring
costs PER PRODUCT, feeding a per-product margin/profit calculation — same
idea as app.models.product_order_daily_statistic.ProductOrderDailyStatistic
being the per-SKU slice of OrderDailyStatistic. Populated in the SAME import
pass as the store-level table (see accrual_report_import.py) — no extra
parsing, since every row already carries a SKU before the store-level
aggregation throws that detail away.

Aggregated at import time down to one row per (ozon_sku, accrual_date,
service_group, charge_type) — same reasoning as the store-level table:
the planner only ever needs a per-day/per-category sum per product, never
the raw per-order rows."""
from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ProductAccrualReportDailyStatistic(TimestampMixin, Base):
    __tablename__ = "product_accrual_report_daily_statistics"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "ozon_sku", "accrual_date", "service_group", "charge_type",
            name="uq_product_accrual_report_daily_store_sku_date_group_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    ozon_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    accrual_date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    service_group: Mapped[str] = mapped_column(String(100), nullable=False)
    charge_type: Mapped[str] = mapped_column(String(150), nullable=False)

    amount_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    rows_count: Mapped[int] = mapped_column(nullable=False, default=0)

    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual_xlsx_upload")

    store = relationship("Store")
