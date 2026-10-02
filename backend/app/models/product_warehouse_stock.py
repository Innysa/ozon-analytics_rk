"""Per-(store, SKU, warehouse) FBO stock snapshot — ДОБАВЛЕНО 2026-10-02 по
прямой просьбе пользователя: отдельная вкладка «Остатки», детализация по
складу, не только суммарные fbo_stock/fbs_stock на Product.

Источник — POST /v2/analytics/stock_on_warehouses (CONFIRMED живым
прогоном backend/scripts/probe_stocks.py на реальном магазине 2026-10-02,
контракт раньше нигде в проекте не проверялся): по каждому (SKU, склад)
Ozon отдаёт free_to_sell_amount («доступно к продаже»), reserved_amount
(«зарезервировано» — уже заказано, ждёт отгрузки покупателю),
promised_amount — поле без официального описания под рукой, но по
смыслу названия и структуре ответа (значение > 0 у части строк, когда
free_to_sell/reserved = 0) — похоже на «обещанный», то есть ожидаемый к
поступлению на этот склад остаток ("в пути на склад Ozon"), который
пользователь прямо просила показать отдельно; ТРЕБУЕТ дальнейшего
подтверждения на реальном примере с promised_amount > 0 прежде чем
подписывать это пользователю как факт, а не гипотезу — см. README.

Снимок, не история: как и ProductPriceDailySnapshot, каждая синхронизация
ПЕРЕЗАПИСЫВАЕТ текущее состояние по (store, sku, warehouse_id), не
накапливает — Ozon не отдаёт историю остатков по датам через этот метод,
только текущий срез."""
from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ProductWarehouseStock(TimestampMixin, Base):
    __tablename__ = "product_warehouse_stocks"
    __table_args__ = (
        UniqueConstraint("store_id", "ozon_sku", "warehouse_id", name="uq_product_warehouse_stock_store_sku_warehouse"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    ozon_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    offer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cluster_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cluster_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    free_to_sell_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    promised_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    store = relationship("Store")
