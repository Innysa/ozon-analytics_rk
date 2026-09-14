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
sharing ONE `accrued_category`, and even one shared `accrual_id` can span
multiple "Группа услуг" (one accrual_id had both a "Продажи" row and a
"Вознаграждение Ozon" row in the XLSX). Whether that finer breakdown
exists inside the API's own `posting` field (unexamined so far) is what
backend/scripts/lookup_accrual_records.py exists to answer — until then,
commission_rub/delivered_sum_rub are NOT computed from this table, only
the whole-day total and the coarse category split are stored.

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

    source: Mapped[str] = mapped_column(String(30), nullable=False)  # "ozon_seller_api"
