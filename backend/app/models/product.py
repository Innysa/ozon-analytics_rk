from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("store_id", "ozon_sku", name="uq_product_store_sku"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    ozon_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ozon_product_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    offer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # seller article
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    price_rub: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    old_price_rub: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    fbo_stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fbs_stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    store = relationship("Store")
