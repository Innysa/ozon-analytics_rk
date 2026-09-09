"""Per-product daily order/revenue/cancellation statistics — the same Ozon
Seller API postings data as OrderDailyStatistic (app.models.order_daily_
statistic; see its own docstring for the confirmed field-level contract),
sliced by ozon_sku instead of summed across the whole store. Populated in the
SAME sync pass as OrderDailyStatistic (see aggregate_postings_by_sku_and_day
in app.services.order_daily_sync_service) — no extra Ozon API calls, since
each posting's products[] already carries a sku per line before the
store-level table throws that detail away.

Deliberately does not carry cost_of_delivered_rub (Себестоимость/Маржа is a
separate concern, computed at the store level on the Дашборд from
Product.cost_price_rub — see OrderDailyStatistic) — this table only answers
"how much of this product sold, per day", not margin.
"""
from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ProductOrderDailyStatistic(TimestampMixin, Base):
    __tablename__ = "product_order_daily_statistics"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "ozon_sku", "date", "delivery_schema",
            name="uq_product_order_daily_stat_store_sku_date_schema",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    ozon_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    delivery_schema: Mapped[str] = mapped_column(String(10), nullable=False)  # "FBO" | "FBS"

    ordered_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ordered_sum_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # по old_price (без скидки)
    ordered_sum_discounted_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # по price (с учётом скидки)

    delivered_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_sum_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    cancelled_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_sum_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    unfinished_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commission_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    source: Mapped[str] = mapped_column(String(30), nullable=False)  # "ozon_seller_api"

    store = relationship("Store")
