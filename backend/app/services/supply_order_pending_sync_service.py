"""Syncs Product.pending_supply_units — total quantity of a SKU sitting
in supply orders ("заявки на поставку") that are NOT YET SHIPPED —
ДОБАВЛЕНО 2026-10-02 по прямой просьбе пользователя after the «FBO
ожидается» (promised_amount, see ProductWarehouseStock) turned out to
mean "уже отгружено и едет", NOT "создано, но ещё не отгружено".

CONFIRMED live (2026-10-02/03, backend/scripts/probe_supply_order_
details.py + probe_supply_order_bundle.py, real account) as a THREE-step
chain — no single Ozon method returns per-SKU quantity for a supply
order directly:

  1. POST /v3/supply-order/list (OzonSellerClient.list_supply_orders) —
     filter.states=[1] confirmed to return real draft/unshipped order
     IDs (the specific semantic MEANING of state=1 is not catalogued
     anywhere accessible — just that it reliably returns orders matching
     "не отгружено" in spirit, cross-checked against the FBO ожидается=0
     vs «В заявках на поставку»=239 real-account comparison that first
     separated these two concepts). Only state=1 is used here; other
     "not yet shipped" states may exist and be missed — see README.
  2. POST /v3/supply-order/get (one order_id at a time — only ever tried
     with exactly one) — order-level metadata, no sku/quantity anywhere,
     but has a nested content.bundle_id (exact path not pinned down,
     extracted via a generic recursive key search below).
  3. POST /v1/supply-order/bundle (the real bundle_id, NOT the order_id)
     — THIS is what finally returns [{"sku": ..., "quantity": ...}, ...],
     confirmed against the user's own cabinet screenshot ("Состав
     заявки", "Всего N SKU").

Full-replace snapshot, same convention as ProductWarehouseStock: every
product's pending_supply_units is reset to its fresh total (0 if the SKU
isn't in any pending order right now) on every successful run — Ozon has
no history for this, only current state.

Potentially MANY Ozon calls (2 extra per supply order on top of the one
list call) — MAX_ORDERS caps a single run so a store with an unusually
large number of open supply orders doesn't turn one click into an
unbounded number of requests; a store that legitimately exceeds it will
undercount until a later run (surfaced in SyncOutcome.errors, not
silent)."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.product import Product
from app.services.ozon.exceptions import OzonAPIError, OzonAuthError, OzonFeatureUnavailable

# Only value confirmed to return real unshipped orders — see this
# module's own docstring for why this isn't a fuller catalogued list.
PENDING_STATES = [1]
PAGE_LIMIT = 100
MAX_ORDERS = 200


def _find_key(obj, key: str):
    """Recursively finds the first value for `key` anywhere in a nested
    dict/list response — used to pull bundle_id out of /v3/supply-order/
    get without assuming exactly where it sits in the structure (same
    discipline as probe_supply_order_details.py's identical helper)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_key(item, key)
            if found is not None:
                return found
    return None


def _find_record_list(obj):
    """Same generic non-empty-list-of-dicts walker as accrual_daily_sync_
    service's _find_record_list — reused here (duplicated, not imported,
    same reasoning as that module's own comment on why) to pull the
    sku/quantity rows out of /v1/supply-order/bundle's response without
    assuming its exact top-level key."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj
    if isinstance(obj, dict):
        for value in obj.values():
            found = _find_record_list(value)
            if found is not None:
                return found
    return None


@dataclass
class SupplyOrderPendingSyncOutcome:
    orders_fetched: int = 0
    orders_with_bundle: int = 0
    skus_updated: int = 0
    errors: list[str] = field(default_factory=list)
    hard_failure: bool = False


def sync_supply_order_pending_quantities(db: Session, *, store_id: str, client) -> SupplyOrderPendingSyncOutcome:
    outcome = SupplyOrderPendingSyncOutcome()

    order_ids: list[int] = []
    last_id = ""
    try:
        while len(order_ids) < MAX_ORDERS:
            data = client.list_supply_orders(states=PENDING_STATES, limit=PAGE_LIMIT, last_id=last_id)
            page_ids = data.get("order_ids") or []
            order_ids.extend(page_ids)
            last_id = data.get("last_id") or ""
            if not page_ids or not last_id:
                break
    except (OzonAuthError, OzonFeatureUnavailable) as exc:
        outcome.errors.append(str(exc))
        outcome.hard_failure = True
        return outcome
    except OzonAPIError as exc:
        outcome.errors.append(str(exc))
        outcome.hard_failure = True
        return outcome

    order_ids = order_ids[:MAX_ORDERS]
    outcome.orders_fetched = len(order_ids)

    totals: dict[str, int] = {}
    for order_id in order_ids:
        try:
            get_data = client.get_supply_order(order_id)
        except OzonAPIError as exc:
            outcome.errors.append(f"заявка {order_id}: /v3/supply-order/get: {exc}")
            continue

        bundle_id = _find_key(get_data, "bundle_id")
        if not bundle_id:
            outcome.errors.append(f"заявка {order_id}: bundle_id не найден в ответе")
            continue

        try:
            bundle_data = client.get_supply_order_bundle(bundle_id)
        except OzonAPIError as exc:
            outcome.errors.append(f"заявка {order_id}: /v1/supply-order/bundle: {exc}")
            continue

        items = _find_record_list(bundle_data)
        if not items:
            continue
        outcome.orders_with_bundle += 1
        for item in items:
            sku = item.get("sku")
            qty = item.get("quantity")
            if sku is None or qty is None:
                continue
            totals[str(sku)] = totals.get(str(sku), 0) + int(qty)

    products = db.query(Product).filter(Product.store_id == store_id).all()
    for product in products:
        product.pending_supply_units = totals.get(product.ozon_sku, 0)
    outcome.skus_updated = len(products)

    return outcome
