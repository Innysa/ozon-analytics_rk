"""Store-wide «Локализация» (% локальных продаж), pulled from Ozon Seller
API's POST /v1/rating/summary — CONFIRMED against a real account (2026-09-11,
via backend/scripts/debug_rating_summary.py, run by the store owner
themselves since this sandbox's own network egress to api-seller.ozon.ru is
blocked):

  - Request: empty body `{}`.
  - Response includes `localization_index`, a nested object:
    `{"localization_percentage": 65, "calculation_date": "2026-09-04T00:00:00Z"}`.
    `localization_percentage` is already a percentage (0-100), not a 0-1
    fraction. Per an earlier (separate) Ozon support answer, this field can
    come back empty if there were no sales in the last 14 days — treat a
    missing/null `localization_index` as "no data", not zero.
  - The literal `/v1/rating/` path an Ozon support-chat answer once named as
    "the" method for this does NOT exist for this account (confirmed 404/
    not-found via the same diagnostic run) — `/v1/rating/summary` is the
    real, working method.
  - Confirmed ACCOUNT-WIDE, not per-product: the response carries a single
    percentage for the whole store, no per-SKU breakdown. This is why
    `localization_pct` only ever appears on the planner's "Итого" row
    (app.services.product_planner_service) — a genuine dead end for the
    per-product figure the seller originally wanted (Ozon's own UI-only
    "Локальность продаж" report is still the only place to see that).

One row per store (upserted on each manual sync, not a history table —
`calculation_date` is Ozon's own last-recalculated date, not when this app
last fetched it; `fetched_at` tracks that separately)."""

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid


class StoreRatingSummary(TimestampMixin, Base):
    __tablename__ = "store_rating_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    localization_pct: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    localization_calculation_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
