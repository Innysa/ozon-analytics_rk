"""Per-day, per-(«Группа услуг», «Тип начисления») sums from Ozon's own
downloadable «Начисления» report (Кабинет → Финансы → Начисления →
«Скачать отчёт», XLSX) — confirmed against a real 21-day export
(2026-09-22, one store, ~40 700 rows) to carry these exact columns:
`ID начисления`, `Дата начисления`, `Группа услуг`, `Тип начисления`,
`Артикул`, `SKU`, `Название товара`, `Количество`, `Цена продавца`,
`Дата принятия заказа в обработку или оказания услуги`, `Платформа
продажи`, `Схема работы`, `Вознаграждение Ozon, %`, `Индекс локализации, %`,
`Среднее время доставки, часы`, `Сумма итого, руб.`.

This is a GENUINELY DIFFERENT, more precise source than
CashFlowStatementPeriod (see that model's own docstring): the ДДС
(cash-flow) report only exposes a lump `services`/`others` bucket with raw
Ozon internal item names (`MarketplaceServiceItem...`) that had to be
guessed at by substring, is grouped into Ozon's own fixed ~weekly payout
periods (never aligned to a dashboard's chosen date range, hence the
day-count PRORATE ESTIMATE dashboard_service.py has always had to apply),
and — CONFIRMED 2026-09-22 by comparing both reports for the same real
21-day window — simply does not reconcile number-for-number against this
report at all (different accounting basis: ДДС is cash movement over
Ozon's own settlement weeks, «Начисления» is per-operation accrual for an
arbitrary caller-chosen range). This report, by contrast, gives:
  - a real per-row `Дата начисления` (calendar day, not a weekly bucket) —
    letting a dashboard date range be summed EXACTLY, with zero prorating;
  - `Группа услуг`/`Тип начисления` in Ozon's own human-readable Russian
    naming, EXACTLY matching the tree shown in the cabinet's own
    «Начисления» screen (confirmed 2026-09-22 against a real screenshot:
    groups "Продажи", "Возвраты", "Вознаграждение Ozon", "Услуги
    доставки", "Продвижение и реклама", "Услуги партнёров", "Услуги FBO",
    "Другие услуги и штрафы" all matched exactly, rub-for-rub, against the
    cabinet's own numbers for the same period) — no substring-matching
    against internal Ozon item names needed at all, and critically
    «Эквайринг» is its OWN `Тип начисления` row inside «Услуги партнёров»,
    not something that has to be teased out of a mixed bucket.

No confirmed Ozon Seller API method returns this same (date, group, type)
granularity — `/v1/finance/realization/posting`'s fields were checked
2026-09-20 and found to be a DIFFERENT, coarser per-order schema (see
docs/ozon-seller-api-methods.md, "Поиск «Услуги доставки» через методы
realization/accrual окончательно закрыт") — so, same as product
localization (see product_localization_import.py's own docstring), this
can only be imported the way the user already downloads it from her own
cabinet: a manual XLSX re-upload, not an automatic sync.

Stored PRE-AGGREGATED by (store_id, accrual_date, service_group,
charge_type) rather than one row per the file's own ~40k product-level
rows — the dashboard only ever needs a per-day/per-category sum, never a
per-SKU breakdown, and aggregating at import time keeps the table small
(a few hundred rows per month) regardless of how many products/orders
contributed. A row's `rows_count` keeps how many raw file rows fed into it,
for diagnostics only.

Unique key is (store_id, accrual_date, service_group, charge_type) — NOT
tied to the uploaded file's own period range. This deliberately lets the
user re-upload overlapping ranges (e.g. "1–12.09" now, "1–21.09" next
week) with no risk of double-counting: a re-upload simply REPLACES the sum
for whichever (day, group, type) triples it covers, the same upsert
pattern as CashFlowStatementPeriod uses for its own periods."""
from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class AccrualReportDailyStatistic(TimestampMixin, Base):
    __tablename__ = "accrual_report_daily_statistics"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "accrual_date", "service_group", "charge_type",
            name="uq_accrual_report_daily_store_date_group_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)

    accrual_date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    service_group: Mapped[str] = mapped_column(String(100), nullable=False)  # Ozon's own "Группа услуг", e.g. "Услуги партнёров"
    charge_type: Mapped[str] = mapped_column(String(150), nullable=False)  # Ozon's own "Тип начисления", e.g. "Эквайринг"

    amount_rub: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    rows_count: Mapped[int] = mapped_column(nullable=False, default=0)

    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual_xlsx_upload")

    store = relationship("Store")
