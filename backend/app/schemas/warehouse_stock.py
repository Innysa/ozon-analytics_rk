from pydantic import BaseModel


class WarehouseStockRowOut(BaseModel):
    ozon_sku: str
    offer_id: str | None
    warehouse_id: int | None
    warehouse_name: str
    cluster_name: str | None
    free_to_sell_amount: int
    reserved_amount: int
    promised_amount: int

    model_config = {"from_attributes": True}


class ProductWarehouseStockSummaryOut(BaseModel):
    """One row per SKU — totals across all its warehouses, plus the
    per-warehouse breakdown for expanding in the UI."""

    ozon_sku: str
    offer_id: str | None
    name: str | None
    fbo_free_to_sell_total: int
    fbo_reserved_total: int
    fbo_promised_total: int
    fbs_stock: int | None
    pending_supply_units: int | None
    warehouses: list[WarehouseStockRowOut]


class WarehouseStockListResponse(BaseModel):
    items: list[ProductWarehouseStockSummaryOut]
    synced_at: str | None  # SyncRun.finished_at of the last successful sync, ISO — None if never synced
