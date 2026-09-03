import enum

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ChangeType(str, enum.Enum):
    MAIN_PHOTO = "main_photo"
    SEO = "seo"
    TITLE = "title"
    DESCRIPTION = "description"
    PRICE = "price"
    CHARACTERISTICS = "characteristics"
    ADVERTISING = "advertising"
    OTHER = "other"


class ChangeHistory(TimestampMixin, Base):
    """Manually logged product-card changes, for later correlation with sales/ads/reviews."""

    __tablename__ = "change_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    change_type: Mapped[ChangeType] = mapped_column(Enum(ChangeType, name="change_type"), nullable=False)
    changed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    product = relationship("Product")
