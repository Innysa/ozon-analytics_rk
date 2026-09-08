from decimal import Decimal

from pydantic import BaseModel, Field


class ProductOut(BaseModel):
    id: str
    store_id: str
    ozon_sku: str
    ozon_product_id: int | None
    offer_id: str | None
    name: str
    image_url: str | None
    price_rub: Decimal | None
    old_price_rub: Decimal | None
    cost_price_rub: Decimal | None
    fbo_stock: int | None
    fbs_stock: int | None
    is_archived: bool

    model_config = {"from_attributes": True}


class ProductCostPriceIn(BaseModel):
    cost_price_rub: Decimal | None = Field(default=None, ge=0)
