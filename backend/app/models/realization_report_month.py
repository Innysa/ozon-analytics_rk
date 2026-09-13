"""Ozon's official monthly settlement report ("Отчёт о реализации товаров"
— POST /v2/finance/realization), archived once per (store, calendar month).

CONFIRMED against a real account (2026-09-13, via backend/scripts/
probe_realization_report.py):

  - Request: `{"year": <int>, "month": <int>}` — top-level fields, NOT
    nested under a "date" object (an earlier guess for the same shape
    Ozon's cash-flow-statement method uses).
  - The report is only available for a CLOSED calendar month — a real
    call for the CURRENT, still-in-progress month returned Ozon's own 404
    "Report was not found" (this is Ozon's real answer, not a guess — see
    OzonSellerClient._post()'s own 404-handling comment for why the exact
    response body matters here and isn't paved over with a generic
    guess). The same call for the PREVIOUS, already-closed month
    succeeded. This is why sync/parsing (still TODO — see below) can only
    ever run for months that have already ended, never the current one;
    MarginBlock.is_preliminary on the Дашборд exists specifically to be
    honest about that gap in the meantime.
  - Full response SHAPE (top-level fields, pagination, and every field on
    a line item) is NOT YET fully confirmed — only a truncated tail of one
    real response has been seen so far (one item's fields: offer_id,
    seller_price_per_instance, delivery_commission {amount, compensation,
    commission, bonus, standard_fee, total, stars, bank_coinvestment,
    pick_up_point_coinvestment}, return_commission, commission_ratio).
    raw_payload below stores the COMPLETE response specifically so this
    doesn't need a fresh Ozon call once the shape IS fully confirmed —
    commission_rub/delivered_sum_rub can be computed retroactively from
    already-archived months.

commission_rub/delivered_sum_rub are deliberately NOT columns here yet —
adding them before the real field mapping is confirmed would mean
guessing at what to put in them, the exact discipline this project has
repeatedly burned itself on (see CashFlowStatementPeriod's own docstring
for the multi-round history of getting a single field wrong that way).
They get added in a follow-up migration once probe_realization_report.py
has captured a complete, unambiguous example.

One row per (store, year, month) — upserted, not a history table, since
each request is inherently for a fixed already-closed period whose data
does not change afterward."""
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid


class RealizationReportMonth(TimestampMixin, Base):
    __tablename__ = "realization_report_months"
    __table_args__ = (
        UniqueConstraint("store_id", "year", "month", name="uq_realization_report_store_year_month"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-12

    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)  # "ozon_seller_api"
