"""Daily order/revenue/buyout/cancellation statistics, pulled automatically
from Ozon Seller API's postings endpoints (FBO: POST /v2/posting/fbo/list,
FBS: POST /v3/posting/fbs/list) — one row per (store, day, fulfillment
scheme). This is the "РНП" data source: real orders across the whole store,
not just what's advertised (unlike AdvertisingDailyStatistic) and not
dependent on a manual "Аналитика → Товары" upload (unlike
ProductCardStatistic).

Confirmed against a real account via backend/scripts/debug_orders_finance_api.py
(see that script's own docstring for the full methodology): a posting's
`products[]` carries `sku`/`quantity`/`price` (the actual sale price after
any discount), and its `financial_data.products[]` carries a matching entry
(joined by `product_id` == the product's `sku`) with `old_price` (before
discount), `commission_amount`, `commission_percent`, `payout`. A posting's
own `status` field has confirmed real values "delivered" and "cancelled";
every other status (Ozon has many more granular ones, e.g.
"awaiting_deliver", "acceptance_in_progress") is bucketed here as
"unfinished" — see app.services.order_daily_sync_service's own docstring
for the exact three-way split and what it does NOT yet distinguish.

Deliberately separate rows for FBO vs FBS (never merged) — same precedent
as AdvertisingDailyStatistic vs AdvertisingStatistic: the two fulfillment
schemes come from different endpoints with different completion/latency
characteristics, and silently summing them would hide that.

commission_rub is summed directly from each posting's own financial_data —
confirmed present there, so no separate call to Ozon's Finance API
(/v3/finance/transaction/list) is needed for this specific number. Whether
commission_amount already accounts for quantity > 1 per line, or is a flat
per-line figure regardless of quantity, is UNCONFIRMED (every real example
seen so far happened to have quantity=1) — see the sync service docstring.

cost_of_delivered_rub is computed by joining each delivered posting's line
items to Product.cost_price_rub at sync time — sellers must enter that
manually (see Product model's own docstring; no Ozon API exposes it).
cost_of_delivered_known_units tracks how many delivered units actually HAD
a cost price set at sync time, so the UI can tell "0 ₽ себестоимость"
(genuinely free) apart from "себестоимость ещё не введена для этих
товаров" (unknown) — comparing it to delivered_units is how the UI decides
which case applies.
"""
from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class OrderDailyStatistic(TimestampMixin, Base):
    __tablename__ = "order_daily_statistics"
    __table_args__ = (
        UniqueConstraint("store_id", "date", "delivery_schema", name="uq_order_daily_stat_store_date_schema"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    delivery_schema: Mapped[str] = mapped_column(String(10), nullable=False)  # "FBO" | "FBS"

    ordered_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ordered_sum_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # по old_price (без скидки)
    ordered_sum_discounted_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # по price (с учётом скидки)

    delivered_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_sum_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    cost_of_delivered_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    cost_of_delivered_known_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    cancelled_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_sum_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    unfinished_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    commission_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    source: Mapped[str] = mapped_column(String(30), nullable=False)  # "ozon_seller_api"
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    store = relationship("Store")
