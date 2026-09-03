from sqlalchemy import ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid


class AIGeneration(TimestampMixin, Base):
    """Usage/cost/error log for every call made to an AIProvider — used for
    the analytics required by the spec (requests, tokens, errors, latency, cost)."""

    __tablename__ = "ai_generations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    review_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("reviews.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    operation: Mapped[str] = mapped_column(String(50), nullable=False)  # analyze_review | generate_reply | rewrite_reply | ...
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_rub: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)

    success: Mapped[bool] = mapped_column(nullable=False, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
