"""Daily snapshot of Product.price_rub/old_price_rub/fbo_stock/fbs_stock —
one row per (store, SKU, calendar day), captured every time the catalog
syncs (`POST /v3/product/info/list`, same call that already updates
`Product` itself — see app.api.routes.sync's `_upsert()`).

CONFIRMED 2026-09-22 (via real Ozon Seller cabinet screenshots + the
user's own reference tool showing the same product's price moving from
2400 to 2700 ₽ over two weeks — Ozon's algorithmic/"эластичный бустинг"
pricing changes the seller's own price day to day): `Product.price_rub` is
only ever a SINGLE current snapshot (last catalog sync), not history — so
the «СПП (расчёт)» base built from it (see OrderDailyStatistic's own
docstring) silently used TODAY's price for every historical day being
synced, understating accuracy more the further back an order's date was
from the last sync. There is no Ozon API that returns a price's value on
a past date directly — the only way to have it is to start recording it
ourselves, one row per real sync.

Deliberately a snapshot keyed by calendar DATE, not tied 1:1 to a
SyncRun — a day with multiple manual syncs just overwrites that day's row
(last value of the day wins), same upsert convention as every other daily
statistic in this app. A day with NO sync at all (e.g. before this
feature existed, or a gap in the schedule) simply has no row — callers
(see order_daily_sync_service.py's seller_price_by_sku_and_date) fall back
to Product's current price_rub for any (sku, date) this table doesn't
cover, rather than fabricating one.

fbo_stock/fbs_stock captured here too — free (same sync call, same loop),
laying groundwork for a possible future "остатки по дням" feature without
a second sync pass, though nothing reads them from here yet."""
from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ProductPriceDailySnapshot(TimestampMixin, Base):
    __tablename__ = "product_price_daily_snapshots"
    __table_args__ = (
        UniqueConstraint("store_id", "ozon_sku", "date", name="uq_product_price_daily_snapshot_store_sku_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    ozon_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)

    price_rub: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    old_price_rub: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    fbo_stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fbs_stock: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped[str] = mapped_column(String(30), nullable=False, default="ozon_seller_api")

    store = relationship("Store")
