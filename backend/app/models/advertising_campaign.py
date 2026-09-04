"""Advertising campaigns synced from Ozon Performance API (GET /api/client/campaign).

Only campaign metadata (name, type, state, budget, dates) is synced here — no
daily performance numbers. Day-by-day metrics (advertising_daily_metrics,
still a placeholder in app.models.future) require the Performance API's
asynchronous statistics-report flow, which is not implemented yet; this table
is deliberately limited to what has been verified against Ozon's real API.
"""
from sqlalchemy import Date, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class AdvertisingCampaign(TimestampMixin, Base):
    __tablename__ = "advertising_campaigns"
    __table_args__ = (UniqueConstraint("store_id", "ozon_campaign_id", name="uq_campaign_store_ozon_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    ozon_campaign_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Raw string values as returned by Ozon (e.g. advObjectType, CAMPAIGN_STATE_RUNNING) —
    # kept as free text rather than a hardcoded enum, since the full set of
    # possible values was not exhaustively confirmed against current docs.
    campaign_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    daily_budget_rub: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    date_from: Mapped[Date | None] = mapped_column(Date, nullable=True)
    date_to: Mapped[Date | None] = mapped_column(Date, nullable=True)

    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    store = relationship("Store")
