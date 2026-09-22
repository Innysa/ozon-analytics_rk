"""AI-generated holistic analysis of ONE product's card — combines review
findings (already aggregated by app.services.analytics_service) with
already-collected advertising (AdvertisingDailyStatistic) and order
(ProductOrderDailyStatistic) daily trends for that product's SKU, using the
same AIProvider used everywhere else (app.services.ai) — see
app.services.product_card_ai_review_service for the generation logic.

ADDED 2026-09-22, requested by the store owner: the existing "Рекомендации
ИИ" tab on a product's card only ever analyzed review text — she wanted a
full picture that also reasons about whether impressions/clicks/CTR/orders
are rising or falling for that product, not just what reviews complain
about. No new Ozon API call: all three input sources were already being
collected automatically for other pages (Реклама/Продажи tabs, "Аналитика
отзывов").

Same storage pattern as AdvertisingAiReview — one row per (store, product,
period_start, period_end), manually triggered only (no scheduled daily run:
running this automatically for every product in a store would be a lot of
AI calls for data that doesn't necessarily change meaningfully day to day;
the store owner asked for a button, not an automatic job, for this one) —
see app.api.routes.products' POST .../ai-review/generate."""
from sqlalchemy import Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ProductCardAiReview(TimestampMixin, Base):
    __tablename__ = "product_card_ai_reviews"
    __table_args__ = (
        UniqueConstraint("store_id", "product_id", "period_start", "period_end", name="uq_product_card_ai_review_store_product_period"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)

    period_start: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[Date] = mapped_column(Date, nullable=False, index=True)

    overview: Mapped[str] = mapped_column(Text, nullable=False)
    trend_observations_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # list[str] — ad/order trend commentary
    hypotheses_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # list[str] — explicitly-labeled, unverified links (e.g. review complaint <-> falling orders)
    recommendations_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # list[str]

    reviews_considered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)

    store = relationship("Store")
    product = relationship("Product")
