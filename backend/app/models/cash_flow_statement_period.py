"""Weekly cash-flow ("ДДС" — движение денежных средств) periods, pulled
automatically from Ozon Seller API's POST /v1/finance/cash-flow-statement/
list — the replacement for the now-confirmed-obsolete POST /v3/finance/
transaction/list (see OzonSellerClient.list_finance_transactions()'s own
docstring). This is the source for Логистика/Хранение и прочие
услуги/Возврат-логистика on the Дашборд — data the order postings
(OrderDailyStatistic) never carried.

CONFIRMED against a real account (two rounds of real diagnostic output,
2026-09-10, via backend/scripts/debug_cash_flow_statement.py — see that
script's own docstring and README's "Заказы и финансы" section for the
full methodology):

  - Request: {"date": {"from": <ISO ts>, "to": <ISO ts>}, "page",
    "page_size", "with_details": true}. The monthly form
    ({"date": {"year", "month"}}) does NOT work — Ozon requires date.from/
    date.to even though the report is inherently periodic.
  - Response: {"result": {"cash_flows": [...], "page_count",
    "details": [...]}}. Ozon does NOT split by the requested date range —
    it returns its OWN fixed weekly periods (confirmed: 7-day windows,
    e.g. 2026-08-24..2026-08-30, 2026-08-17..2026-08-23, stepping
    backward — almost certainly tied to Ozon's seller payout cycle, not a
    calendar-week or the caller's own date.from/date.to). A period
    straddling the requested range's edge still comes back whole; there is
    no confirmed way to get a partial-period figure.
  - `cash_flows[]` items: {"period": {"id", "begin", "end"}, "orders_amount",
    "returns_amount", "commission_amount", "services_amount",
    "item_delivery_and_return_amount", "currency_code"}. NOT stored as the
    primary numbers here (orders_amount/returns_amount would duplicate
    OrdersRevenueBlock, commission_amount would duplicate MarginBlock's own
    commission from postings) — kept only for completeness/cross-checking.
  - `details[]` items are matched to `cash_flows[]` by their OWN
    `period.begin`/`period.end` (CONFIRMED — each details[] entry carries
    its own period object; do NOT assume positional/index correlation with
    cash_flows[], that was an open question this session resolved by
    checking a real response rather than guessing). Confirmed shape:
    {"period", "begin_balance_amount", "payments": [{"payment",
    "currency_code"}], "delivery": {"total", "amount", "delivery_services":
    {"total", "items": [{"name", "price"}]}}, "return": {"total", "amount",
    "return_services": {"total", "items": [...]}}, "loan", "invoice_transfer", "rfbs": {"total",
    "transfer_delivery", "transfer_delivery_return",
    "compensation_delivery_return", "partial_compensation",
    "partial_compensation_return"}, "services": {"total", "items":
    [{"name", "price"}]}}.
  - `delivery.delivery_services` is the confirmed Логистика figure — real
    item names seen: MarketplaceServiceItemDirectFlowLogisticSum,
    MarketplaceServiceItemRedistributionLastMileCourier,
    MarketplaceServiceItemRedistributionDropoff,
    MarketplaceServiceItemDeliveryToHandoverPlaceOzon.
  - `services` is a MIXED bucket, NOT purely storage/penalties — real item
    names seen include MarketplaceServiceItemTemporaryStorageRedistribution
    (storage), MarketplaceServiceCostPerClick (advertising, already
    double-counted by AdvertisingDailyStatistic if summed in!), and an
    "InsuranceSe..." (insurance) entry. There is no confirmed per-category
    subtotal inside it — only a lump `total` plus a raw `items[]` list.
    Deliberately NOT split into "Хранение"/"Штрафы" here: doing so would
    require guessing which item `name` values belong to which category,
    and only a handful of names have been observed on one real account —
    an unrecognized future name would be silently mis-bucketed. Shown as
    one honest "Прочие услуги" figure instead, with the raw items kept in
    services_items_json for anyone who wants to inspect them. No item name
    resembling a "penalty"/"fine"/"штраф" has been observed yet — if Ozon
    exposes one at all through this method, it has not been confirmed.
  - CORRECTED (2026-09-12, real account raw_payload — located via
    inspect_cash_flow_periods.py --find-key since a full JSON dump couldn't
    be copied off a VNC console with no scrollback): `return` is a
    TOP-LEVEL sibling of `delivery` inside a `details[]` entry, NOT nested
    inside delivery as `delivery.return` — that was this docstring's own
    earlier, admittedly unconfirmed ("parsed ... defensively") guess, and
    it was wrong: reading `delivery.get("return")` always got {} on a real
    account, so delivery_return_total was NEVER populated at all (always
    None) — "Обработка возвратов" showed 0 while genuine return-handling
    spend existed the whole time. The real shape mirrors delivery: `return
    = {"total", "amount", "return_services": {"total", "items": [{"name",
    "price"}]}}`. Confirmed exactly on a real period: return.total (-81
    414.52) == return.amount (-51 662.52) + return_services.total
    (-29 752) — i.e. return.total is already the bucket's grand total,
    inclusive of return_services, not a sibling figure to add on top of it.
    Confirmed real items: MarketplaceServiceItemRedistributionReturnsPVZ
    (return processing via a pickup point — exact parent unconfirmed, kept
    alongside return_services's own items when merging into
    delivery_return_items_json) and, inside return.return_services.items[],
    MarketplaceServiceItemReturnFlowLogistic ("Обратная логистика" — cost
    of shipping the returned item back).
  - UPDATE (2026-09-10, user's own grep of their full saved diagnostic
    file, not a partial screenshot): `services.items[]` DOES contain a
    fine — real item name `FinesShipmentNonRecommendedSlot`. It is stored
    as-is inside services_items_json (raw storage, no special-casing at
    the DB layer); the Дашборд layer (dashboard_service.py) is what now
    pulls "Fine"-named items out of it into their own Штрафы figure — see
    that module's own docstring for the categorization rule.
  - Also confirmed same session: `details[]` entries carry a SIXTH
    top-level bucket alongside delivery/services/rfbs/loan/invoice_transfer
    that earlier rounds never saw — `others`: {"total", "items": [{"name",
    "price"}]}. Real items seen: MarketplaceRedistributionOfAcquiringOperation
    (эквайринг) and MarketplaceSellerDecompensationItemByTypeDocOperation
    (декомпенсация продавца). Stored as-is in others_total/
    others_items_json, shown on the Дашборд as "Прочие удержания" — not
    further split by name (only two examples seen so far, not enough to
    safely sub-categorize).

Uniqueness is on (store_id, period_begin, period_end) — NOT period.id,
because every real example seen so far had "id": 0 regardless of which
week the period actually was; it is not a stable identifier.

items_json columns store the raw `items[]` array as JSON text (same
raw-storage convention as raw_payload elsewhere in this codebase) rather
than a separate child table — small, per-period lists that exist purely
for transparency/drill-down, not queried independently.
"""
from sqlalchemy import Date, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class CashFlowStatementPeriod(TimestampMixin, Base):
    __tablename__ = "cash_flow_statement_periods"
    __table_args__ = (
        UniqueConstraint("store_id", "period_begin", "period_end", name="uq_cash_flow_period_store_range"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    period_begin: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[Date] = mapped_column(Date, nullable=False, index=True)

    # From cash_flows[] — kept for completeness/cross-checking, NOT shown
    # directly on the Дашборд (would duplicate OrdersRevenueBlock/MarginBlock).
    orders_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    returns_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    commission_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    services_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    item_delivery_and_return_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    currency_code: Mapped[str] = mapped_column(String(10), nullable=False, default="RUB")

    # From details[] — the actually-displayed figures.
    begin_balance_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    delivery_total: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    delivery_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    delivery_services_total: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)  # Логистика
    delivery_services_items_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_return_total: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)  # Возврат (логистика)
    delivery_return_items_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    services_total: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)  # Прочие услуги + Штрафы + Хранение (raw, pre-split)
    services_items_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    others_total: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)  # Прочие удержания
    others_items_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    rfbs_total: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    loan: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    invoice_transfer: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)

    source: Mapped[str] = mapped_column(String(30), nullable=False)  # "ozon_seller_api"
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # full {"cash_flow": ..., "details": ...} JSON

    store = relationship("Store")
