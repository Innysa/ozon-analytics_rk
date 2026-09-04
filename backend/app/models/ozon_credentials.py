from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class OzonCredentials(TimestampMixin, Base):
    """Per-store Ozon credentials for two independent API surfaces: Seller API
    (Client-Id/Api-Key, used for reviews) and Performance API (Client-Id/
    Client-Secret, used for advertising). Every secret is stored encrypted
    (see app.core.encryption) and is never serialized back to the frontend in
    full — only a masked preview. The two surfaces have entirely separate
    connection-check state since they are unrelated Ozon services."""

    __tablename__ = "ozon_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    # Nullable: a row can exist with only Performance API credentials set (or
    # vice versa) — the two API surfaces are configured independently.
    client_id_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    last_connection_check_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_connection_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_connection_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviews_api_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Performance API (advertising) — separate credential pair, separate connection state.
    performance_client_id_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    performance_client_secret_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    performance_last_connection_check_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    performance_last_connection_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    performance_last_connection_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    store = relationship("Store", back_populates="ozon_credentials")
