from datetime import date

from pydantic import BaseModel


class ProductCardStatisticOut(BaseModel):
    id: str
    product_id: str | None
    product_name: str | None = None
    ozon_sku: str
    offer_id: str | None
    brand: str | None
    category_l1: str | None
    fulfillment_scheme: str | None
    date: date

    impressions_total: int | None
    impressions_search_catalog: int | None
    card_visits: int | None
    cart_adds_total: int | None
    ordered_units: int | None
    delivered_units: int | None
    bought_out_units: int | None
    cancelled_units_by_order_date: int | None
    returned_units_by_order_date: int | None
    ordered_sum_actual_price_rub: float | None

    search_catalog_position_ozon: float | None
    conv_impression_to_order_pct_ozon: float | None
    conv_to_cart_total_pct_ozon: float | None
    conv_cart_to_order_pct_ozon: float | None
    conv_order_to_buyout_pct_ozon: float | None

    avg_price_rub: float | None
    price_index_label_ozon: str | None
    drr_pct_ozon: float | None
    stock_end_of_period: int | None
    reviews_count: int | None
    rating: float | None

    model_config = {"from_attributes": True}


class ProductCardStatisticListResponse(BaseModel):
    items: list[ProductCardStatisticOut]
    total: int


class ProductCardAnalyticsOut(BaseModel):
    has_data: bool
    date_from: date | None = None
    date_to: date | None = None

    # Facts, summed over the period.
    total_impressions: int = 0
    total_card_visits: int = 0
    total_cart_adds: int = 0
    total_ordered_units: int = 0
    total_delivered_units: int = 0
    total_bought_out_units: int = 0
    total_cancelled_units: int = 0
    total_returned_units: int = 0
    total_ordered_sum_rub: float = 0

    # Latest known snapshot within the period (point-in-time values — not additive).
    latest_stock: int | None = None
    latest_reviews_count: int | None = None
    latest_rating: float | None = None
    latest_avg_price_rub: float | None = None
    latest_price_index_label: str | None = None

    # Calculated by this app from the summed counts above — an app-defined
    # formula, NOT a reproduction of Ozon's own (undisclosed / more complex)
    # per-row conversion methodology, which a real sample showed does not
    # reduce to a simple ratio of the counts it reports alongside it.
    cart_conversion_calculated_pct: float | None = None  # cart adds / impressions
    order_conversion_calculated_pct: float | None = None  # orders / cart adds
    buyout_rate_calculated_pct: float | None = None  # bought out / ordered

    rating_trend: list[dict] = []  # [{"date": ..., "rating": ...}]
    stock_trend: list[dict] = []  # [{"date": ..., "stock": ...}]
