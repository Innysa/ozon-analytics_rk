import enum

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class RecommendationKind(str, enum.Enum):
    PRODUCT_IMPROVEMENT = "product_improvement"
    CARD_IMPROVEMENT = "card_improvement"
    INFOGRAPHIC_IDEA = "infographic_idea"
    GENERAL = "general"


class Recommendation(TimestampMixin, Base):
    """AI-derived recommendation, explicitly linked to the reviews it was derived
    from so the UI can show 'show source reviews' and never present it as fact."""

    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True)

    kind: Mapped[RecommendationKind] = mapped_column(Enum(RecommendationKind, name="recommendation_kind"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_review_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    product = relationship("Product")
