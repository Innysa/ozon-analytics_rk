from datetime import date

from pydantic import BaseModel


class OrderDailyStatisticOut(BaseModel):
    id: str
    date: date
    delivery_schema: str

    ordered_units: int
    ordered_sum_rub: float
    ordered_sum_discounted_rub: float

    delivered_units: int
    delivered_sum_rub: float
    cost_of_delivered_rub: float
    cost_of_delivered_known_units: int

    cancelled_units: int
    cancelled_sum_rub: float

    unfinished_units: int
    commission_rub: float

    model_config = {"from_attributes": True}


class OrderDailyStatisticListResponse(BaseModel):
    items: list[OrderDailyStatisticOut]
    total: int


class ProductOrderDailyStatisticOut(BaseModel):
    id: str
    ozon_sku: str
    date: date
    delivery_schema: str

    ordered_units: int
    ordered_sum_rub: float
    ordered_sum_discounted_rub: float

    delivered_units: int
    delivered_sum_rub: float

    cancelled_units: int
    cancelled_sum_rub: float

    unfinished_units: int
    commission_rub: float

    model_config = {"from_attributes": True}


class ProductOrderDailyStatisticListResponse(BaseModel):
    items: list[ProductOrderDailyStatisticOut]
    total: int
