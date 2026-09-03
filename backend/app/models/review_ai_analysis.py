from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ReviewAIAnalysis(TimestampMixin, Base):
    """Structured AI output for a single review (see app.services.ai.schemas.ReviewAnalysisResult)."""

    __tablename__ = "review_ai_analysis"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    review_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    sentiment: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    urgency: Mapped[str] = mapped_column(String(10), nullable=False)
    reply_needed: Mapped[bool] = mapped_column(nullable=False)

    advantages_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    complaints_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_improvements_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_improvements_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    hypotheses_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)

    review = relationship("Review", back_populates="ai_analysis")
