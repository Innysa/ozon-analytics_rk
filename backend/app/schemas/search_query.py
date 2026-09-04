from datetime import date

from pydantic import BaseModel


class SearchQueryStatisticOut(BaseModel):
    id: str
    product_id: str | None
    product_name: str | None = None
    ozon_sku: str
    offer_id: str | None
    query_text: str
    period_start: date
    period_end: date

    people_searched: int | None
    people_saw: int | None
    position_ozon: float | None
    conv_search_to_card_pct_ozon: float | None
    conv_search_to_order_pct_ozon: float | None
    ordered_units_by_query: int | None
    ordered_sum_by_query_rub: float | None

    model_config = {"from_attributes": True}


class SearchQueryStatisticListResponse(BaseModel):
    items: list[SearchQueryStatisticOut]
    total: int


class TopQueryItem(BaseModel):
    query_text: str
    people_searched: int | None
    people_saw: int | None
    position_ozon: float | None
    ordered_units_by_query: int | None
    ordered_sum_by_query_rub: float | None


class SearchQueryAnalyticsOut(BaseModel):
    has_data: bool
    period_start: date | None = None
    period_end: date | None = None

    distinct_queries: int = 0

    # Facts, summed over all imported query rows in range.
    total_people_searched: int = 0
    total_people_saw: int = 0
    total_ordered_units: int = 0
    total_ordered_sum_rub: float = 0

    # Calculated by this app from the summed counts above. NOT a
    # reproduction of Ozon's own per-row conversion percentages
    # (conv_search_to_order_pct_ozon etc.) — a real sample showed this
    # simple ratio (ordered units / people searched) differs by roughly two
    # orders of magnitude from Ozon's own reported "Конверсия из поиска в
    # заказ", meaning Ozon's methodology counts something other than raw
    # search occurrences (likely unique sessions/visits) in the
    # denominator. This app does not guess at that methodology — it
    # reports its own explicit ratio and never claims it matches Ozon's.
    order_rate_calculated_pct: float | None = None
    avg_position_calculated: float | None = None

    top_queries_by_searches: list[TopQueryItem] = []
    top_queries_by_orders: list[TopQueryItem] = []
