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

ordered_sum_seller_price_rub / ordered_sum_discounted_for_known_seller_
price_rub / ordered_units_with_known_seller_price — added 2026-09-20 to fix
README's "Известная проблема" for «СПП (расчёт)»: that column used to be
computed on the frontend from ordered_sum_rub (= sum of `old_price`, i.e.
Ozon's own "Цена до скидки" — an inflated reference/strikethrough price),
which CONFIRMED on a real account (two real SKUs, exact match: our stored
old_price_rub == the account's own "Цены и акции" export's "Цена до
скидки" column, 13000==13000 and 7000==7000) is NOT the base Ozon's own
cabinet uses for its "СПП" percentage — that base is "Ваша цена" (the
seller's own current price), which the SAME export showed running ~40-48%
discount for actually-selling SKUs vs the ~65-79% our old formula produced
using "Цена до скидки" as the base — matching the user's own complaint
(cabinet showed 40-53%, we showed ~75%).

"Ваша цена" isn't present in ANY posting/order field at all (postings only
carry `price`/`old_price` = the sale price and the inflated reference) —
the closest available approximation is Product.price_rub, populated from
the SEPARATE catalog sync (/v3/product/info/list's own `price` field).
CONFIRMED close but not exact against the same real export (within ~3.2-
3.4% on two real SKUs, e.g. our price_rub=6000 vs the export's "Ваша
цена"=6200) — plausibly ordinary price-change drift between the export's
timestamp and the catalog's last sync, not a wrong field. Still a real
improvement over the confirmed-wrong "Цена до скидки" basis, not a
guess dressed up as one.

Because Product.price_rub is a CURRENT snapshot (not the seller's price on
the historical order's own date), and not every ordered SKU is guaranteed
to still exist in our Product table (deleted/never-synced offer), each
order line's seller price is looked up at sync time and MAY be missing —
exactly the same "known vs unknown" split as cost_of_delivered_known_units
above, not silently treated as 0: ordered_sum_seller_price_rub and
ordered_sum_discounted_for_known_seller_price_rub are summed ONLY over
lines where a price was found (so the ratio between them stays internally
consistent — never mixing a "known-price" numerator against an "all-units"
denominator or vice versa), and ordered_units_with_known_seller_price
tracks how many units that covers, so the UI can flag when it's less than
ordered_units instead of silently presenting a partial-coverage percentage
as if it covered every order.
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

    # См. докстринг класса — база для правильного «СПП (расчёт)», ПОДМНОЖЕСТВО
    # ordered_units/ordered_sum_discounted_rub (только строки с известной ценой продавца).
    ordered_sum_seller_price_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    ordered_sum_discounted_for_known_seller_price_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    ordered_units_with_known_seller_price: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

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
