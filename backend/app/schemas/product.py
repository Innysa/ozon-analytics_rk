from pydantic import BaseModel


class ProductOut(BaseModel):
    id: str
    store_id: str
    ozon_sku: str
    offer_id: str | None
    name: str
    image_url: str | None

    model_config = {"from_attributes": True}
