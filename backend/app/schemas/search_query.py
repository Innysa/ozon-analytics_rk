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
    query_index: int | None = None

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


class SearchQueryPositionOut(BaseModel):
    """One (SKU, query) pair's most recent position snapshot plus the one
    immediately before it, for the "Позиции в поиске" tab. Built entirely
    from the existing SearchQueryStatistic rows (period_end is the report's
    date-of-record) — no separate table: every upload already adds a new
    period_end snapshot rather than overwriting, so history was already
    being kept; this is just a comparison over that same data.

    position_change = previous_position - position: positive means the
    product moved UP (position number got smaller, e.g. 97 -> 81 is +16);
    negative means it moved down. None when there is no earlier snapshot to
    compare against for this exact (SKU, query) pair — never fabricated."""

    ozon_sku: str
    offer_id: str | None
    product_id: str | None
    product_name: str | None = None
    query_text: str

    report_date: date  # period_end of the current (most recent) snapshot
    position: float | None
    previous_report_date: date | None
    previous_position: float | None
    position_change: float | None
    position_change_direction: str | None  # "up" (improved) | "down" (worsened) | None (unchanged/no data)

    people_searched: int | None
    people_saw: int | None
    ordered_units_by_query: int | None
    query_index: int | None = None  # API-only: Ozon's own query-importance rank for this SKU, NOT the search position


class SearchQueryPositionListResponse(BaseModel):
    items: list[SearchQueryPositionOut]
    total: int


class QueryCompetitorRowOut(BaseModel):
    """One row of Ozon's "Результаты по запросу" export — the full search
    result page for one query, competitors included. See
    app.services.search_query_competitors_service; deliberately NOT stored
    in the database (stateless preview only) and NOT integrated with the
    position-history/highlighting logic above — a different, simpler,
    lower-priority feature ("who else shows up for this query")."""

    position: int
    ozon_product_id: int | None
    product_name: str | None
    seller_name: str | None
    overall_score: float | None
    status: str | None
    cpc_bid_rub: float | None
    strategy: str | None
    cpo_bid_text: str | None
    relevance_pct: float | None
    reviews_text: str | None
    price_rub: float | None
    popularity_score: float | None
    ozon_promotions: str | None
    delivery_term: str | None
    price_index_pct: float | None


class QueryCompetitorsReportOut(BaseModel):
    query_text: str
    region: str | None
    generated_at: date | None
    positions_in_results: int | None
    rows: list[QueryCompetitorRowOut]
