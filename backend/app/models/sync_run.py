import enum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid


class SyncStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class SyncSourceType(str, enum.Enum):
    OZON_API = "ozon_api"
    CSV_IMPORT = "csv_import"
    XLSX_IMPORT = "xlsx_import"


class SyncRun(TimestampMixin, Base):
    __tablename__ = "sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    initiated_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    source_type: Mapped[SyncSourceType] = mapped_column(Enum(SyncSourceType, name="sync_source_type"), nullable=False)
    status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus, name="sync_status"), default=SyncStatus.RUNNING, nullable=False)

    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reviews_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reviews_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reviews_skipped_duplicate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
