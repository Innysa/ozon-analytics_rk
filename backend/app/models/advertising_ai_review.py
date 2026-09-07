"""AI-generated overview of a store's Ozon advertising campaigns, produced
by the same AIProvider used for review analysis (app.services.ai) — see
app.services.advertising_ai_review_service for the generation logic and
app.services.advertising_ai_review_scheduler for the daily automatic run.

Same storage pattern as ReviewAIAnalysis (structured scalar columns for the
fields the UI filters/sorts on, `_json` text columns for list fields) except
keyed by (store_id, period_start, period_end) instead of a single review —
one row per generation covering a rolling lookback window, not per-campaign,
so re-running for the same period updates in place (see the unique
constraint) while a new period_end each day builds history without
overwriting older reviews, same as SearchQueryStatistic.

Deliberately does NOT include orders/ДРР/ROAS yet — see the service
module's own docstring for why (input data limitation, not a modeling
choice)."""
from sqlalchemy import Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class AdvertisingAiReview(TimestampMixin, Base):
    __tablename__ = "advertising_ai_reviews"
    __table_args__ = (
        UniqueConstraint("store_id", "period_start", "period_end", name="uq_advertising_ai_review_store_period"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    period_start: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    campaigns_analyzed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    overview: Mapped[str] = mapped_column(Text, nullable=False)
    insights_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # list of {ozon_campaign_id, campaign_name, assessment, note}
    anomalies_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # list[str]
    recommendations_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # list[str]

    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)

    store = relationship("Store")
