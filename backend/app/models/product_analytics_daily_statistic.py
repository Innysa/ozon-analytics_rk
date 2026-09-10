"""Per-product daily funnel statistics — the automatically-collected
counterpart of ProductCardStatistic (the manual "Аналитика → Товары" CSV
upload), pulled from Ozon Seller API's POST /v1/analytics/data with
dimension=["sku", "day"]. See app.services.ozon.client.OzonSellerClient
.get_analytics_data()'s own docstring and app.services.ozon.schemas
.OzonAnalyticsDataResponse for the confirmed request/response contract.

Requires an Ozon Premium Plus/Premium Pro subscription — without it, this
API only exposes revenue/ordered_units (already covered by the free
order_daily_statistics sync), not the funnel metrics below. See
product_analytics_daily_sync_service's own docstring for the exact metric
list and the fixed request order the values here are indexed from.

Deliberately a SEPARATE table from ProductCardStatistic, never merged: same
precedent as AdvertisingStatistic vs. AdvertisingDailyStatistic and
OrderDailyStatistic's own CSV/API split elsewhere in this app — the two
sources have different metric definitions (Ozon does not document these as
identical) and mixing them would misrepresent which number came from where.

cart_conversion_pdp_pct and position_category are Ozon's OWN per-row
percentage/position values (like drr_pct_ozon elsewhere in this app) — never
recomputed or averaged across days when aggregating a period; only summed
counts (views/cart adds/sessions/ordered units/revenue) are valid to add
together.
"""
from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ProductAnalyticsDailyStatistic(TimestampMixin, Base):
    __tablename__ = "product_analytics_daily_statistics"
    __table_args__ = (
        UniqueConstraint("store_id", "ozon_sku", "date", name="uq_product_analytics_daily_stat_store_sku_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    ozon_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)

    revenue_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # "revenue"
    ordered_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # "ordered_units"
    views_pdp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # "hits_view_pdp" — показы на карточке
    cart_adds_pdp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # "hits_tocart_pdp" — в корзину с карточки
    cart_conversion_pdp_pct: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)  # "conv_tocart_pdp" (Ozon's own %)
    sessions_pdp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # "session_view_pdp" — уникальные посетители карточки
    position_category: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)  # "position_category" (Ozon's own позиция)

    source: Mapped[str] = mapped_column(String(30), nullable=False)  # "ozon_seller_api"

    store = relationship("Store")
