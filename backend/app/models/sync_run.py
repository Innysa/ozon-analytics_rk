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
    OZON_API = "ozon_api"  # Ozon Seller API (reviews)
    OZON_ADVERTISING_API = "ozon_advertising_api"  # Ozon Performance API (campaigns)
    CSV_IMPORT = "csv_import"
    XLSX_IMPORT = "xlsx_import"


class SyncRun(TimestampMixin, Base):
    """Log of a single sync attempt, shared by every module that pulls data
    from an external source (reviews from Ozon/CSV/XLSX, advertising
    campaigns from Ozon Performance API, ...). Field names are generic
    ("items_*") on purpose so the same table serves all of them."""

    __tablename__ = "sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    initiated_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    source_type: Mapped[SyncSourceType] = mapped_column(Enum(SyncSourceType, name="sync_source_type"), nullable=False)
    status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus, name="sync_status"), default=SyncStatus.RUNNING, nullable=False)

    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_skipped_duplicate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
