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

"Выручка"/"Продажи"/"Возвраты" are NOT yet computed from this table —
which specific field(s) reproduce those cabinet figures is still
unconfirmed (same script exists to find them; see its own docstring).
Until then, Dashboard/РНП still use OrderDailyStatistic's postings-based
delivered_sum_rub for revenue, only commission_rub is overridden by this
table's commission_ozon_rub where a synced day exists — see
dashboard_service._commission_rub_for_period's own docstring.

One row per (store, date) — upserted daily (and re-synced for a short
trailing window, not just once) since Ozon can revise recent accruals
after the fact (e.g. a return processed a day later adjusts that
original day's total) — see accrual_daily_sync_service's own docstring."""
from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
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

    source: Mapped[str] = mapped_column(String(30), nullable=False)  # "ozon_seller_api"
