"""Daily accrual totals from Ozon Seller API's POST /v1/finance/accrual/
by-day — see OzonSellerClient.get_accrual_by_day()'s own docstring for the
confirmed request contract.

CONFIRMED 2026-09-13 on a real account ("Комфорт дом"): summing
`total_amount.amount` across every record returned for one day matched
Ozon's own cabinet "Всего к выплате за период" figure for that same day
to the kopeck (296 656.65 ₽ vs 296 657 ₽ — the ~0.35 ₽ difference is
rounding, not a discrepancy). This is the first genuinely TRUE-DAILY
financial figure this project has — unlike /v2/finance/realization
(monthly only) and unlike OrderDailyStatistic's own commission_rub/
delivered_sum_rub (postings-derived, confirmed to drift from Ozon's own
totals by several percent — see MarginBlock.is_preliminary's own
docstring for that investigation).

total_amount_rub is the CONFIRMED, validated whole-day total. by_category_
json is a {"POSTING": ..., "NON_ITEM": ..., "ITEM": ...}-shaped breakdown
of that same total by `accrued_category` — these three values ARE
directly confirmed present on real records, but this categorization is
CONFIRMED TOO COARSE to isolate "Комиссия Ozon" from revenue/logistics/
advertising: a real account's own "Начисления" XLSX export shows those as
separate "Группа услуг" values (e.g. "Вознаграждение Ozon" summing to
-389 940.02 ₽, matching the cabinet's own per-line breakdown exactly) all
sharing ONE `accrued_category`.

commission_ozon_rub — CONFIRMED 2026-09-14 via `backend/scripts/
find_accrual_commission_field.py` against a real account's real day
(12.09.2026): summing `posting.products[].commission.sale_commission.
amount` (a duplicate field, `commission.commission.amount` on the same
product, carries the identical value) across EVERY product in EVERY
record's `posting` for the day reproduces "Вознаграждение Ozon" from the
cabinet exactly (-389 940.02 ₽). Stored with the SAME sign Ozon itself
uses (negative — a deduction), matching OrderDailyStatistic.commission_
rub's own sign convention, so both can be added into MarginBlock's
margin_rub formula the same way. A record with no `posting` (e.g. a
NON_ITEM charge not tied to a specific shipment) contributes 0, not an
error — see accrual_daily_sync_service._extract_commission_ozon_rub's
own docstring.

sales_rub/returns_rub — REOPENED AND CONFIRMED 2026-09-19, exactly the
"differently-shaped record on some other day" the 2026-09-14 closure
above said would be needed to reopen this: NOT from accrual/by-day at
all (that search stays correctly closed — accrual/by-day itself still
exposes no revenue field), but from the separate POST /v1/finance/
realization/by-day method (see OzonSellerClient.get_realization_by_day's
own docstring for the confirmed request/response contract). Confirmed
on TWO different real days (12.09.2026 and 05.09.2026, same account):
Σ(seller_price_per_instance × delivery_commission.quantity) matched the
cabinet's "Продажи" to the kopeck both times, and Σ(seller_price_per_
instance × return_commission.quantity), negated, matched "Возвраты"
both times — see accrual_daily_sync_service._extract_realization_revenue.
Stored with the SAME sign convention as the cabinet (sales_rub positive,
returns_rub negative) and as commission_ozon_rub (a deduction is
negative), so delivered_sum_rub-equivalent revenue for a synced day is
sales_rub + returns_rub. Populated by the SAME sync call as commission_
ozon_rub (sync_accrual_daily_statistic also fetches realization/by-day
now) but from an independent endpoint — a failure fetching one does not
block the other; see that function's own docstring.

revenue_confirmed — NOT the same signal as "this row exists": a row can
exist (and have a real commission_ozon_rub) purely from a SUCCESSFUL
accrual/by-day fetch while realization/by-day, fetched separately, FAILED
for that same day, leaving sales_rub/returns_rub at their 0/0 default —
which would look like a confirmed "zero revenue day" to a naive reader
instead of "not fetched". This flag is set True only when realization/
by-day was actually fetched successfully for this row at least once, and
is never cleared by a later failed re-fetch (see sync_accrual_daily_
statistic — sales_rub/returns_rub/revenue_confirmed are only written when
realization_fetched is True). dashboard_service._revenue_rub_for_period
checks this flag, not merely row existence, before trusting sales_rub/
returns_rub over OrderDailyStatistic's postings estimate.

"Продвижение и реклама"/"Услуги доставки" are STILL not derivable from
either accrual/by-day or realization/by-day — realization/by-day's rows
only carry the "Продажи"/"Возвраты"/"Вознаграждение Ozon" groups, not
these two, per the same 2026-09-19 check. Продвижение и реклама is
already tracked separately via the Performance API (AdvertisingDailyStatistic);
Услуги доставки has no confirmed daily API source yet.

One row per (store, date) — upserted daily (and re-synced for a short
trailing window, not just once) since Ozon can revise recent accruals
after the fact (e.g. a return processed a day later adjusts that
original day's total) — see accrual_daily_sync_service's own docstring."""
from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid


class AccrualDailyStatistic(TimestampMixin, Base):
    __tablename__ = "accrual_daily_statistics"
    __table_args__ = (
        UniqueConstraint("store_id", "date", name="uq_accrual_daily_store_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    date: Mapped[Date] = mapped_column(Date, nullable=False)

    total_amount_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    by_category_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commission_ozon_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    sales_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    returns_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    revenue_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    source: Mapped[str] = mapped_column(String(30), nullable=False)  # "ozon_seller_api"
