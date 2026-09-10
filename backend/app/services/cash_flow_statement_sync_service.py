"""Orchestrates the automatic cash-flow-statement sync from Ozon Seller
API's POST /v1/finance/cash-flow-statement/list — see
app.models.cash_flow_statement_period.CashFlowStatementPeriod's own
docstring for the full CONFIRMED request/response contract (two rounds of
real diagnostic output against a real account, 2026-09-10) and what is
deliberately NOT parsed (e.g. no attempt to split "services" into
Хранение/Штрафы — that bucket only exposes a lump total + a raw items[]
list, no per-category subtotal).

Ozon returns its OWN fixed ~weekly periods regardless of the requested
date.from/date.to — this sync always requests the full configured lookback
window in one call (paginating only if page_count > 1) rather than
iterating day-by-day; a store's periods rarely exceed
CASH_FLOW_STATEMENT_MAX_PAGES * 1000 rows.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.cash_flow_statement_period import CashFlowStatementPeriod
from app.services.ozon.exceptions import OzonAPIError

PAGE_LIMIT = 1000


@dataclass
class SyncOutcome:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_ozon_ts(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _items_json(container: dict | None) -> str | None:
    """container is e.g. details.delivery.delivery_services — {"total", "items"}."""
    if not container:
        return None
    items = container.get("items")
    if not items:
        return None
    return json.dumps(items, ensure_ascii=False)


def _fetch_all(client, *, date_from: str, date_to: str, max_pages: int) -> tuple[list[dict], list[dict]]:
    all_flows: list[dict] = []
    all_details: list[dict] = []
    for page in range(1, max_pages + 1):
        data = client.get_cash_flow_statement(date_from=date_from, date_to=date_to, page=page, page_size=PAGE_LIMIT, with_details=True)
        result = data.get("result") or {}
        flows = result.get("cash_flows") or []
        details = result.get("details") or []
        all_flows.extend(flows)
        all_details.extend(details)
        page_count = result.get("page_count") or 1
        if page >= page_count:
            break
    return all_flows, all_details


def sync_cash_flow_statement_periods(
    db: Session,
    *,
    store_id: str,
    client,
    date_from: date | None = None,
    date_to: date | None = None,
) -> SyncOutcome:
    settings = get_settings()
    outcome = SyncOutcome()

    today = datetime.now(timezone.utc).date()
    resolved_date_to = date_to or today
    resolved_date_from = date_from or (resolved_date_to - timedelta(days=settings.CASH_FLOW_STATEMENT_DEFAULT_LOOKBACK_DAYS - 1))
    date_from_ts = f"{resolved_date_from.isoformat()}T00:00:00Z"
    date_to_ts = f"{resolved_date_to.isoformat()}T23:59:59Z"

    try:
        flows, details = _fetch_all(client, date_from=date_from_ts, date_to=date_to_ts, max_pages=settings.CASH_FLOW_STATEMENT_MAX_PAGES)
    except OzonAPIError as exc:
        outcome.errors.append(str(exc))
        return outcome

    # details[] entries are matched to cash_flows[] entries by their OWN
    # period.begin/period.end — CONFIRMED against a real response, not
    # positional (see CashFlowStatementPeriod's own docstring).
    details_by_range = {}
    for d in details:
        period = d.get("period") or {}
        details_by_range[(period.get("begin"), period.get("end"))] = d

    outcome.fetched = len(flows)

    for flow in flows:
        period = flow.get("period") or {}
        begin_raw, end_raw = period.get("begin"), period.get("end")
        period_begin = _parse_ozon_ts(begin_raw)
        period_end = _parse_ozon_ts(end_raw)
        if period_begin is None or period_end is None:
            continue

        detail = details_by_range.get((begin_raw, end_raw)) or {}
        delivery = detail.get("delivery") or {}
        delivery_services = delivery.get("delivery_services") or {}
        delivery_return = delivery.get("return") or {}
        services = detail.get("services") or {}
        rfbs = detail.get("rfbs") or {}

        existing = (
            db.query(CashFlowStatementPeriod)
            .filter(
                CashFlowStatementPeriod.store_id == store_id,
                CashFlowStatementPeriod.period_begin == period_begin,
                CashFlowStatementPeriod.period_end == period_end,
            )
            .first()
        )
        record = existing or CashFlowStatementPeriod(
            store_id=store_id, period_begin=period_begin, period_end=period_end, source="ozon_seller_api"
        )

        record.orders_amount = flow.get("orders_amount") or 0
        record.returns_amount = flow.get("returns_amount") or 0
        record.commission_amount = flow.get("commission_amount") or 0
        record.services_amount = flow.get("services_amount") or 0
        record.item_delivery_and_return_amount = flow.get("item_delivery_and_return_amount") or 0
        record.currency_code = flow.get("currency_code") or "RUB"

        record.begin_balance_amount = detail.get("begin_balance_amount")
        record.delivery_total = delivery.get("total")
        record.delivery_amount = delivery.get("amount")
        record.delivery_services_total = delivery_services.get("total")
        record.delivery_services_items_json = _items_json(delivery_services)
        record.delivery_return_total = delivery_return.get("total")
        record.delivery_return_items_json = _items_json(delivery_return)
        record.services_total = services.get("total")
        record.services_items_json = _items_json(services)
        record.rfbs_total = rfbs.get("total")
        record.loan = detail.get("loan")
        record.invoice_transfer = detail.get("invoice_transfer")
        record.raw_payload = json.dumps({"cash_flow": flow, "details": detail}, ensure_ascii=False)

        if existing:
            outcome.updated += 1
        else:
            db.add(record)
            outcome.created += 1

    db.commit()
    return outcome
