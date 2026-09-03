from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class Store(TimestampMixin, Base):
    __tablename__ = "stores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    memberships = relationship("StoreMembership", back_populates="store", cascade="all, delete-orphan")
    ozon_credentials = relationship(
        "OzonCredentials", back_populates="store", uselist=False, cascade="all, delete-orphan"
    )
    ai_settings = relationship(
        "StoreAISettings", back_populates="store", uselist=False, cascade="all, delete-orphan"
    )
