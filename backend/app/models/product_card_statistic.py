"""Product-card ("Аналитика → Товары") daily statistics, imported from
Ozon's own export — confirmed against a real file (one product, 7 days:
sales funnel, conversions, orders/delivery/buyout/cancellations/returns,
price, stock, reviews, rating). Unlike the advertising statistics export,
this one is genuinely per-day (column "День"), so no period-aggregation
compromise is needed here — one row per (product, date).

Fields ending in `_ozon` are conversion/rate percentages Ozon computes and
reports itself — kept as-is, never recomputed, and never blindly averaged
across days when aggregating (see app.services.product_analytics_service,
which only sums the additive count/money fields and derives its own rates
from those sums — the only mathematically valid way to aggregate a
conversion rate over a period).
"""
from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ProductCardStatistic(TimestampMixin, Base):
    __tablename__ = "product_card_statistics"
    __table_args__ = (
        UniqueConstraint("store_id", "ozon_sku", "date", name="uq_card_stat_store_sku_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)

    ozon_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    offer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_l1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_l2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_l3: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fulfillment_scheme: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "FBO" | "FBS" | ...

    date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)

    # --- Sales funnel (additive counts — safe to sum for period aggregates) ---
    impressions_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    impressions_search_catalog: Mapped[int | None] = mapped_column(Integer, nullable=True)
    card_visits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cart_adds_from_search_catalog: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cart_adds_from_card: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cart_adds_total: Mapped[int | None] = mapped_column(Integer, nullable=True)

    ordered_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivered_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bought_out_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancelled_units_by_cancel_date: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancelled_units_by_order_date: Mapped[int | None] = mapped_column(Integer, nullable=True)
    returned_units_by_return_date: Mapped[int | None] = mapped_column(Integer, nullable=True)
    returned_units_by_order_date: Mapped[int | None] = mapped_column(Integer, nullable=True)

    ordered_sum_actual_price_rub: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    ordered_sum_list_price_rub: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)

    # --- Ozon-reported rates/positions — never recomputed, never averaged blindly ---
    search_catalog_position_ozon: Mapped[float | None] = mapped_column(Numeric(9, 2), nullable=True)
    conv_impression_to_order_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    conv_search_catalog_to_cart_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    conv_search_catalog_to_card_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    conv_card_to_cart_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    conv_to_cart_total_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    conv_cart_to_order_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    conv_order_to_buyout_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)

    # --- Price, promotion, stock, reputation (point-in-time / descriptive) ---
    avg_price_rub: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    discount_from_median_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    price_index_label_ozon: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "Супер-выгодный"
    promo_days_label_ozon: Mapped[str | None] = mapped_column(String(20), nullable=True)  # e.g. "1 из 1"
    paid_promo_days_label_ozon: Mapped[str | None] = mapped_column(String(20), nullable=True)
    drr_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    stock_end_of_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviews_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)

    abc_by_revenue_ozon: Mapped[str | None] = mapped_column(String(1), nullable=True)  # "A" | "B" | "C"
    abc_by_units_ozon: Mapped[str | None] = mapped_column(String(1), nullable=True)

    source: Mapped[str] = mapped_column(String(20), nullable=False)  # "csv_import" | "xlsx_import"
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    store = relationship("Store")
    product = relationship("Product")
