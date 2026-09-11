"""Manually-entered monthly plan per product — the only user-editable input
for the «РНП Товары» planner page (app.services.product_planner_service
computes everything else: factual figures from ProductOrderDailyStatistic/
AdvertisingDailyStatistic, forecast from the plan + month-to-date pace).

Ozon exposes no "target"/"plan" concept of its own — this is the seller's
own goal-setting, entered once per product per calendar month. Deliberately
stores only MONTHLY figures, not a separate daily one: the planner's "План
День" column is plan_*_month / days_in_that_month, computed at read time
(confirmed from the user's own reference spreadsheet screenshot — День ≈
Месяц / 30), not stored redundantly.

Four plan figures, matching the four metric groups on the planner page:
  - Заказы: plan_orders_units, plan_orders_sum_rub
  - Выкупы: plan_buyouts_units, plan_buyouts_sum_rub
  - Рекламный бюджет: plan_ad_budget_rub (no unit — money only, like
    AdvertisingDailyStatistic.spend_rub)
  - Прибыль: plan_profit_rub (one figure, no unit — the "Прибыль" group on
    the planner page is money-only, unlike Заказы/Выкупы)

All four are nullable — a product can have a plan for some metrics and not
others (e.g. a seller might only ever set a Заказы plan and leave Прибыль
unplanned), and the planner page must show "план не задан" per metric
rather than a fabricated 0.
"""
from sqlalchemy import ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ProductMonthlyPlan(TimestampMixin, Base):
    __tablename__ = "product_monthly_plans"
    __table_args__ = (
        UniqueConstraint("store_id", "product_id", "year", "month", name="uq_product_monthly_plan_store_product_period"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)

    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-12

    plan_orders_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_orders_sum_rub: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    plan_buyouts_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_buyouts_sum_rub: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    plan_ad_budget_rub: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    plan_profit_rub: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)

    store = relationship("Store")
    product = relationship("Product")
