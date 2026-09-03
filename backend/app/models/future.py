"""Structural placeholders for future modules (card/sales analytics + ad
day-by-day metrics).

These tables exist so the schema/architecture is ready, per the spec, but are
NOT populated with fabricated data anywhere in this codebase. Endpoints that
would read them return "no data" rather than synthesizing numbers.

Advertising campaign *metadata* is no longer a placeholder — see
app.models.advertising_campaign.AdvertisingCampaign, synced for real from
Ozon Performance API. AdvertisingDailyMetric below (day-by-day spend/clicks/
orders per campaign) still is: it requires Performance API's asynchronous
statistics-report flow, which isn't implemented yet.
"""
from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid


class ProductDailyMetric(TimestampMixin, Base):
    __tablename__ = "product_daily_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)

    views: Mapped[int | None] = mapped_column(nullable=True)
    orders: Mapped[int | None] = mapped_column(nullable=True)
    revenue_rub: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)


class AdvertisingDailyMetric(TimestampMixin, Base):
    __tablename__ = "advertising_daily_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("advertising_campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)

    spend_rub: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    clicks: Mapped[int | None] = mapped_column(nullable=True)
    views: Mapped[int | None] = mapped_column(nullable=True)
    orders: Mapped[int | None] = mapped_column(nullable=True)


class SearchQuery(TimestampMixin, Base):
    __tablename__ = "search_queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)

    query_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    impressions: Mapped[int | None] = mapped_column(nullable=True)
    clicks: Mapped[int | None] = mapped_column(nullable=True)
