from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class StoreAISettings(TimestampMixin, Base):
    """Per-store customization for AI-generated review replies."""

    __tablename__ = "store_ai_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    brand_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tone_of_voice: Mapped[str] = mapped_column(String(50), default="дружелюбный", nullable=False)
    customer_address_form: Mapped[str] = mapped_column(String(50), default="на Вы", nullable=False)
    reply_length: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)  # short|medium|long
    use_emoji: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    signature: Mapped[str | None] = mapped_column(String(255), nullable=True)

    forbidden_words: Mapped[str | None] = mapped_column(Text, nullable=True)  # newline-separated
    allowed_promises: Mapped[str | None] = mapped_column(Text, nullable=True)
    negative_review_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    warranty_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    return_policy_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    support_contacts: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_facts: Mapped[str | None] = mapped_column(Text, nullable=True)

    store = relationship("Store", back_populates="ai_settings")
