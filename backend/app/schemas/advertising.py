from datetime import date, datetime

from pydantic import BaseModel


class PerformanceCredentialsIn(BaseModel):
    client_id: str
    client_secret: str


class PerformanceCredentialsOut(BaseModel):
    configured: bool
    client_id_masked: str | None = None
    client_secret_masked: str | None = None
    last_connection_check_at: datetime | None = None
    last_connection_ok: bool | None = None
    last_connection_message: str | None = None


class AdvertisingCampaignOut(BaseModel):
    id: str
    ozon_campaign_id: str
    name: str | None
    campaign_type: str | None
    state: str | None
    daily_budget_rub: float | None
    date_from: date | None
    date_to: date | None

    model_config = {"from_attributes": True}


class AdvertisingStatisticOut(BaseModel):
    id: str
    product_id: str | None
    product_name: str | None = None
    product_sku: str | None = None
    campaign_id: str | None
    campaign_name: str | None = None
    ozon_campaign_id: str
    ad_tool: str | None
    placement: str | None
    period_start: date
    period_end: date

    spend_rub: float
    sales_promo_rub: float | None
    units_sold: int | None
    impressions: int | None
    clicks: int | None
    ctr_pct_ozon: float | None
    cart_additions: int | None
    cart_conversion_pct_ozon: float | None

    # Ozon's own reported percentages — never recomputed, kept separate from
    # this app's calculated drr_calculated_pct/roas_calculated (see
    # AdvertisingAnalyticsOut) which are the only ones valid to aggregate.
    drr_promo_pct_ozon: float | None
    drr_total_pct_ozon: float | None
    cost_per_order_rub_ozon: float | None
    avg_cpc_rub_ozon: float | None

    drr_calculated_pct: float | None = None
    roas_calculated: float | None = None

    model_config = {"from_attributes": True}


class AdvertisingStatisticListResponse(BaseModel):
    items: list[AdvertisingStatisticOut]
    total: int


class CampaignBreakdown(BaseModel):
    campaign_id: str
    campaign_name: str
    spend_rub: float
    sales_promo_rub: float
    drr_calculated_pct: float | None
    roas_calculated: float | None


class ProductBreakdown(BaseModel):
    product_id: str
    product_name: str
    spend_rub: float
    sales_promo_rub: float
    drr_calculated_pct: float | None
    roas_calculated: float | None


class MetricComparison(BaseModel):
    """One metric's day-over-day comparison, computed by this app from two
    distinct daily (period_start == period_end) reports for the same
    campaign. direction is None (not "flat") when today == yesterday exactly —
    still a real, non-fabricated fact, just no change to show an arrow for."""

    today: float
    yesterday: float
    delta: float
    delta_pct: float | None  # None when yesterday == 0 — division is undefined, never shown as 0%
    direction: str | None  # "up" | "down" | None


class CampaignDailyComparison(BaseModel):
    date_today: date
    date_yesterday: date
    spend_rub: MetricComparison
    impressions: MetricComparison
    clicks: MetricComparison
    sales_promo_rub: MetricComparison


class CampaignDetailOut(BaseModel):
    campaign_id: str
    has_data: bool

    # Totals summed across every uploaded advertising_statistics row for this
    # campaign, over whatever periods have been uploaded (not necessarily daily).
    total_spend_rub: float = 0
    total_sales_promo_rub: float = 0
    total_impressions: int = 0
    total_clicks: int = 0
    total_units_sold: int = 0
    drr_calculated_pct: float | None = None
    roas_calculated: float | None = None
    period_start: date | None = None
    period_end: date | None = None

    # Present only when at least two distinct dates with period_start ==
    # period_end exist for this campaign — see app.services.advertising_analytics_service.
    daily_comparison: CampaignDailyComparison | None = None
    daily_comparison_unavailable_reason: str | None = None


class AdvertisingAnalyticsOut(BaseModel):
    has_data: bool
    period_start: date | None = None
    period_end: date | None = None

    total_spend_rub: float = 0
    total_sales_promo_rub: float = 0
    total_impressions: int = 0
    total_clicks: int = 0
    total_units_sold: int = 0

    # Calculated by this app (sum(spend)/sum(sales)) — the only ДРР/ROAS
    # figures that are valid across an aggregate of rows.
    drr_calculated_pct: float | None = None
    roas_calculated: float | None = None
    ctr_calculated_pct: float | None = None
    avg_cpc_calculated_rub: float | None = None

    by_campaign: list[CampaignBreakdown] = []
    by_product: list[ProductBreakdown] = []
