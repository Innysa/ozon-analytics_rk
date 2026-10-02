from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.product import Product
from app.models.product_warehouse_stock import ProductWarehouseStock
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.schemas.warehouse_stock import (
    ProductWarehouseStockSummaryOut,
    WarehouseStockListResponse,
    WarehouseStockRowOut,
)

router = APIRouter(prefix="/api/stores/{store_id}/warehouse-stocks", tags=["warehouse-stocks"])


@router.get("", response_model=WarehouseStockListResponse)
def list_warehouse_stocks(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)), db: Session = Depends(get_db)
) -> WarehouseStockListResponse:
    rows = (
        db.query(ProductWarehouseStock)
        .filter(ProductWarehouseStock.store_id == ctx.store_id)
        .order_by(ProductWarehouseStock.ozon_sku, ProductWarehouseStock.warehouse_name)
        .all()
    )
    products_by_sku = {p.ozon_sku: p for p in db.query(Product).filter(Product.store_id == ctx.store_id).all()}

    by_sku: dict[str, list[ProductWarehouseStock]] = {}
    for row in rows:
        by_sku.setdefault(row.ozon_sku, []).append(row)

    items: list[ProductWarehouseStockSummaryOut] = []
    for sku, sku_rows in by_sku.items():
        product = products_by_sku.get(sku)
        items.append(
            ProductWarehouseStockSummaryOut(
                ozon_sku=sku,
                offer_id=sku_rows[0].offer_id,
                name=product.name if product else None,
                fbo_free_to_sell_total=sum(r.free_to_sell_amount for r in sku_rows),
                fbo_reserved_total=sum(r.reserved_amount for r in sku_rows),
                fbo_promised_total=sum(r.promised_amount for r in sku_rows),
                fbs_stock=product.fbs_stock if product else None,
                warehouses=[WarehouseStockRowOut.model_validate(r) for r in sku_rows],
            )
        )
    items.sort(key=lambda r: r.name or "")

    last_sync = (
        db.query(SyncRun)
        .filter(
            SyncRun.store_id == ctx.store_id,
            SyncRun.source_type == SyncSourceType.OZON_STOCK_ON_WAREHOUSES_API,
            SyncRun.status.in_([SyncStatus.SUCCESS, SyncStatus.PARTIAL]),
        )
        .order_by(SyncRun.finished_at.desc())
        .first()
    )

    return WarehouseStockListResponse(
        items=items,
        synced_at=last_sync.finished_at.isoformat() if last_sync and last_sync.finished_at else None,
    )
