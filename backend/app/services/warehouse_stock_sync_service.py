"""Syncs per-warehouse FBO stock from Ozon's POST /v2/analytics/
stock_on_warehouses — see OzonSellerClient.get_stock_on_warehouses's own
docstring for the confirmed request contract and ProductWarehouseStock's
own docstring for why this is a full-replace SNAPSHOT (Ozon has no
history for this), not an accumulating daily statistic.

The top-level key the item list sits under was never directly confirmed
(cropped probe screenshots never showed the very top of the payload) — so,
same discipline as accrual_daily_sync_service's _find_record_list, this
module walks the raw response looking for the first non-empty list of
dicts rather than hardcoding a field name that might silently come back
empty."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.product_warehouse_stock import ProductWarehouseStock
from app.services.ozon.exceptions import OzonAPIError, OzonAuthError, OzonFeatureUnavailable

PAGE_LIMIT = 1000
# Runaway guard — no real account tried here has needed more than a
# handful of pages; "has_next"/total isn't confirmed for this endpoint,
# so pagination stops once a page comes back shorter than PAGE_LIMIT.
MAX_PAGES = 50


def _find_record_list(obj):
    """See accrual_daily_sync_service's identical helper for the full
    rationale — reused here verbatim rather than imported, since the two
    modules' "stop looking" semantics (empty page vs no more data) are
    subtly different enough that a shared import would need its own
    parameterization for no real benefit at this scale."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj
    if isinstance(obj, dict):
        for value in obj.values():
            found = _find_record_list(value)
            if found is not None:
                return found
    return None


@dataclass
class WarehouseStockSyncOutcome:
    fetched: int = 0
    upserted: int = 0
    errors: list[str] = field(default_factory=list)
    hard_failure: bool = False  # auth/feature-tier/internal — see product_catalog_sync_service's own convention


def _fetch_all_items(client) -> list[dict]:
    items: list[dict] = []
    offset = 0
    page = 0
    while page < MAX_PAGES:
        page += 1
        data = client.get_stock_on_warehouses(limit=PAGE_LIMIT, offset=offset)
        page_items = _find_record_list(data)
        if not page_items:
            break
        items.extend(page_items)
        if len(page_items) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    return items


def sync_warehouse_stocks(db: Session, *, store_id: str, client) -> WarehouseStockSyncOutcome:
    outcome = WarehouseStockSyncOutcome()

    try:
        items = _fetch_all_items(client)
    except (OzonAuthError, OzonFeatureUnavailable) as exc:
        outcome.errors.append(str(exc))
        outcome.hard_failure = True
        return outcome
    except OzonAPIError as exc:
        outcome.errors.append(str(exc))
        outcome.hard_failure = True
        return outcome

    outcome.fetched = len(items)
    if not items:
        outcome.errors.append(
            "Ozon не вернул ни одной строки остатков — либо на складах Ozon действительно пусто, "
            "либо не удалось распознать формат ответа (см. backend/scripts/probe_stocks.py)"
        )

    rows: list[ProductWarehouseStock] = []
    for item in items:
        sku = item.get("sku")
        warehouse_id = item.get("warehouse_id")
        if sku is None or warehouse_id is None:
            continue
        rows.append(
            ProductWarehouseStock(
                store_id=store_id,
                ozon_sku=str(sku),
                offer_id=item.get("item_code"),
                warehouse_id=int(warehouse_id),
                warehouse_name=item.get("warehouse_name"),
                cluster_id=item.get("cluster_id"),
                cluster_name=item.get("cluster_name"),
                free_to_sell_amount=int(item.get("free_to_sell_amount") or 0),
                reserved_amount=int(item.get("reserved_amount") or 0),
                promised_amount=int(item.get("promised_amount") or 0),
            )
        )

    # Full-replace snapshot — see ProductWarehouseStock's own docstring:
    # Ozon has no history here, and a SKU/warehouse pair absent from this
    # run (sold out everywhere, delisted, moved off that warehouse) should
    # not linger as a stale row forever.
    db.execute(delete(ProductWarehouseStock).where(ProductWarehouseStock.store_id == store_id))
    db.add_all(rows)
    outcome.upserted = len(rows)

    return outcome
