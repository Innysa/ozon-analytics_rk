"""Advertising performance statistics, imported from Ozon's own "Продвижение →
Статистика" export (CSV/XLSX) — one row per (product SKU, campaign) for a
reporting period. This is the real, verified format of the file a seller can
actually obtain (confirmed against a real exported file), not a guess at the
Performance API's asynchronous statistics-report contract, which was not
possible to verify in enough detail (see app.services.ozon_performance.client
module docstring).

There is no per-day granularity inside a single export — Ozon aggregates over
whatever period the seller selects in the UI. To get genuinely daily numbers,
a seller uploads one export per day (period_start == period_end); the app
does not fabricate a "date" that isn't in the source data. `period_start`/
`period_end` together are the unit of time this row covers.

Money and percentage fields ending in `_ozon` are exactly what Ozon reported
(never recomputed); ДРР/ROAS aggregates computed by this app from spend/sales
sums live in app.services.advertising_analytics_service and are always kept
labeled separately from Ozon's own numbers, consistent with how review
analytics separates fact / calculated / hypothesis.
"""
from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class AdvertisingStatistic(TimestampMixin, Base):
    __tablename__ = "advertising_statistics"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "ozon_sku", "ozon_campaign_id", "period_start", "period_end",
            name="uq_ad_stat_store_sku_campaign_period",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    campaign_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("advertising_campaigns.id", ondelete="SET NULL"), nullable=True, index=True)

    ozon_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ozon_campaign_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    ad_tool: Mapped[str | None] = mapped_column(String(100), nullable=True)  # "Инструмент", e.g. "Оплата за клик"
    placement: Mapped[str | None] = mapped_column(String(100), nullable=True)  # "Место размещения"

    period_start: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[Date] = mapped_column(Date, nullable=False, index=True)

    spend_rub: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    sales_promo_rub: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)  # "Продажи в продвижении, ₽"
    units_sold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sales_promo_model_rub: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)  # "...с заказов модели, ₽"
    units_sold_model: Mapped[int | None] = mapped_column(Integer, nullable=True)

    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctr_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    cart_additions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cart_conversion_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)

    drr_promo_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)  # "ДРР в продвижении, %"
    drr_total_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)  # "ДРР (общий), %"
    cost_per_order_rub_ozon: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    avg_cpc_rub_ozon: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)

    source: Mapped[str] = mapped_column(String(20), nullable=False)  # "csv_import" | "xlsx_import"
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    store = relationship("Store")
    product = relationship("Product")
    campaign = relationship("AdvertisingCampaign")
