import enum

from sqlalchemy import Boolean, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class CommentStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"


class ReviewComment(TimestampMixin, Base):
    """A reply to a review — either an AI-generated draft, a human edit of it,
    or the record of what was actually published to Ozon / copied manually."""

    __tablename__ = "review_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    review_id: Mapped[str] = mapped_column(String(36), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[CommentStatus] = mapped_column(Enum(CommentStatus, name="comment_status"), default=CommentStatus.DRAFT, nullable=False)

    generated_by_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    approved_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    published_via: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "ozon_api" | "manual_copy"
    ozon_comment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    publish_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    review = relationship("Review", back_populates="comments")
