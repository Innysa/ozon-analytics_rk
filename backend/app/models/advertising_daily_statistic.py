"""Daily advertising performance statistics, pulled automatically from Ozon
Performance API's asynchronous statistics-report flow (POST /api/client/
statistics -> poll -> download ZIP of per-campaign CSVs) — one row per
(campaign, SKU, day). Genuinely daily, unlike the CSV-upload-based
AdvertisingStatistic (app.models.advertising_statistic), which is
period-level because that's what the seller's own "Продвижение →
Статистика" export gives.

Deliberately a SEPARATE table from AdvertisingStatistic, not merged into it:
the two sources have different column sets (this one has page_type/
impression_condition/avg_bid, the CSV export has ad_tool/placement/ДРР
percentages/cost_per_order) and, if both are populated for overlapping
campaigns/periods, summing them together would double-count spend and
revenue. Analytics/UI built on top of this table must not silently combine
it with AdvertisingStatistic without an explicit reconciliation decision.

Real, field-tested constraints of the source API (see
app.services.ozon_performance.client and
app.services.advertising_daily_sync_service for where each is enforced):
  - max 10 campaign IDs per statistics-report request
  - max 1 report in flight per Performance API account at a time
  - report is a ZIP containing one CSV per requested campaign, named
    "{campaignId}_{dateFrom}-{dateTo}.csv" (dates DD.MM.YYYY)
  - CSV: ';'-delimited, a title line, a header line, per-day/per-SKU rows,
    and a final "Всего" (total) row — the total row is not imported here
    (same "skip the row Ozon itself didn't mean as data" precedent as the
    "Итого и среднее" row in product_card_statistic's importer): a campaign
    can genuinely have zero impressions in the period, and Ozon still emits
    a fully-zero total row for it, which is not an error.
"""
from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class AdvertisingDailyStatistic(TimestampMixin, Base):
    __tablename__ = "advertising_daily_statistics"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "ozon_campaign_id", "ozon_sku", "date",
            name="uq_ad_daily_stat_store_campaign_sku_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    campaign_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("advertising_campaigns.id", ondelete="SET NULL"), nullable=True, index=True)

    ozon_campaign_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    ozon_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_name: Mapped[str | None] = mapped_column(String(500), nullable=True)  # "Название товара"

    date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)  # "День"

    product_price_rub: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)  # "Цена товара"
    page_type: Mapped[str | None] = mapped_column(String(100), nullable=True)  # "Тип страницы"
    impression_condition: Mapped[str | None] = mapped_column(String(200), nullable=True)  # "Условие показа"

    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)  # "Показы"
    clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)  # "Клики"
    ctr_pct_ozon: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)  # "CTR (%)"
    cart_additions: Mapped[int | None] = mapped_column(Integer, nullable=True)  # "В корзину"
    avg_bid_rub_ozon: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)  # "Средняя ставка (руб.)"
    spend_rub: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)  # "Расход, ₽ с НДС"
    orders: Mapped[int | None] = mapped_column(Integer, nullable=True)  # "Заказы"
    revenue_rub: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)  # "Выручка, ₽"
    orders_model: Mapped[int | None] = mapped_column(Integer, nullable=True)  # "Заказы модели"
    revenue_model_rub: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)  # "Выручка с заказов модели, ₽"

    source: Mapped[str] = mapped_column(String(30), nullable=False)  # "ozon_performance_api"
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    store = relationship("Store")
    product = relationship("Product")
    campaign = relationship("AdvertisingCampaign")
