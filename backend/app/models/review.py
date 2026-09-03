import enum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ReviewStatus(str, enum.Enum):
    NEW = "new"
    PENDING_ANALYSIS = "pending_analysis"
    ANALYZED = "analyzed"
    DRAFT_CREATED = "draft_created"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"
    NO_REPLY_NEEDED = "no_reply_needed"


class ReviewSource(str, enum.Enum):
    OZON_API = "ozon_api"
    CSV_IMPORT = "csv_import"
    XLSX_IMPORT = "xlsx_import"
    DEMO = "demo"


class Review(TimestampMixin, Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("store_id", "ozon_review_id", name="uq_review_store_ozon_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )

    ozon_review_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source: Mapped[ReviewSource] = mapped_column(Enum(ReviewSource, name="review_source"), nullable=False)

    rating: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pros: Mapped[str | None] = mapped_column(Text, nullable=True)
    cons: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    existing_seller_reply: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status"), default=ReviewStatus.NEW, nullable=False, index=True
    )

    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Raw payload as received from Ozon/import, kept for traceability without guessing at a fixed schema.
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    store = relationship("Store")
    product = relationship("Product")
    comments = relationship("ReviewComment", back_populates="review", cascade="all, delete-orphan")
    ai_analysis = relationship(
        "ReviewAIAnalysis", back_populates="review", uselist=False, cascade="all, delete-orphan"
    )
