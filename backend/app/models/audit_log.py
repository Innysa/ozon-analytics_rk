from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid


class AuditLog(TimestampMixin, Base):
    """Append-only log of security- and business-relevant actions. Never store
    secret values here — only outcomes/messages (see app.core.logging.redact)."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("stores.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # e.g. "login", "logout", "sync_started", "review_analyzed", "draft_created",
    # "reply_edited", "reply_approved", "reply_published", "ozon_connection_check"
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    result: Mapped[str] = mapped_column(String(20), nullable=False, default="success")  # success|failure
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
