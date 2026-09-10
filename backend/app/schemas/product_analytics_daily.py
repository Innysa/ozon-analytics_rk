from datetime import date

from pydantic import BaseModel


class ProductAnalyticsDailyStatisticOut(BaseModel):
    id: str
    ozon_sku: str
    date: date

    revenue_rub: float
    ordered_units: int
    views_pdp: int
    cart_adds_pdp: int
    cart_conversion_pdp_pct: float | None
    sessions_pdp: int
    position_category: float | None

    model_config = {"from_attributes": True}


class ProductAnalyticsDailyStatisticListResponse(BaseModel):
    items: list[ProductAnalyticsDailyStatisticOut]
    total: int
