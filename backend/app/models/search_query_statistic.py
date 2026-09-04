"""Search-query analytics, imported from Ozon's own "Аналитика → Запросы"
export (XLSX) — one row per (product SKU, search query) for a reporting
period. Structure verified against a real exported file (single product,
150 distinct queries).

Layout: unlike every other real Ozon export handled by this app, this
sheet is NOT a flat table — it's a hierarchical block. One "product header"
row per product carries SKU/Артикул/Название товара with every metric
column blank, immediately followed by N "query detail" rows for that
product (SKU/Артикул/Название blank; query text + metrics filled). The
importer carries the current product context forward across the detail
rows until the next product header row appears. See
app.services.search_query_import.

There is no per-row date column — like the advertising-statistics export,
Ozon aggregates each query's numbers over whatever period the seller
selects, given only once in a metadata block at the top of the sheet
("Дата начала" / "Дата конца"). period_start/period_end together are the
unit of time this row covers; the app never fabricates a daily date that
isn't in the source data.

conv_*_pct_ozon fields here are exactly the percentage number Ozon's
export renders as text (e.g. "2,98%" parses to 2.98, meaning 2.98%) — NOT
a 0..1 fraction like the analogous *_pct_ozon fields on
ProductCardStatistic (whose source cells are Excel-percent-formatted
floats, i.e. genuinely a fraction under the hood). This file hands back
the percentage as pre-formatted text, so 2.98 is what "2.98%" actually
means here, kept as-is rather than forced into the other file's fraction
convention.
"""
from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class SearchQueryStatistic(TimestampMixin, Base):
    __tablename__ = "search_query_statistics"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "ozon_sku", "query_text", "period_start", "period_end",
            name="uq_search_query_stat_store_sku_query_period",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)

    ozon_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    offer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    query_text: Mapped[str] = mapped_column(String(500), nullable=False, index=True)

    period_start: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[Date] = mapped_column(Date, nullable=False, index=True)

    # --- Additive counts (safe to sum for period aggregates) ---
    people_searched: Mapped[int | None] = mapped_column(Integer, nullable=True)  # "Человек искало"
    people_saw: Mapped[int | None] = mapped_column(Integer, nullable=True)  # "Человек увидело"
    ordered_units_by_query: Mapped[int | None] = mapped_column(Integer, nullable=True)  # "Заказано товаров по запросам"
    ordered_sum_by_query_rub: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)  # "Заказано на сумму по запросам"

    # --- Ozon-reported rates/position — never recomputed, never averaged blindly ---
    position_ozon: Mapped[float | None] = mapped_column(Numeric(9, 2), nullable=True)  # "Позиция товара"
    conv_search_to_card_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    conv_search_to_order_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)

    source: Mapped[str] = mapped_column(String(20), nullable=False)  # "csv_import" | "xlsx_import"
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    store = relationship("Store")
    product = relationship("Product")
