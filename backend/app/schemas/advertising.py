from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, field_validator


class PerformanceCredentialsIn(BaseModel):
    client_id: str
    client_secret: str

    @field_validator("client_id", "client_secret")
    @classmethod
    def _strip_whitespace(cls, v: str) -> str:
        # See OzonCredentialsIn._strip_whitespace — same copy-paste footgun.
        v = v.strip()
        if not v:
            raise ValueError("Значение не может быть пустым")
        return v


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


class CampaignAutoDailyComparison(BaseModel):
    date_today: date
    date_yesterday: date
    spend_rub: MetricComparison
    impressions: MetricComparison
    clicks: MetricComparison
    revenue_rub: MetricComparison


class CampaignAutoDailyDetailOut(BaseModel):
    """Same shape/purpose as CampaignDetailOut below, but aggregated from
    AdvertisingDailyStatistic (Ozon Performance API auto-sync) instead of the
    CSV-uploaded AdvertisingStatistic. Every row here is already exactly one
    day, so — unlike CampaignDetailOut's daily_comparison, which requires
    period_start == period_end to rule out a weekly/monthly upload — the only
    condition for a comparison here is having at least two distinct dates."""

    has_data: bool = False
    total_spend_rub: float = 0
    total_revenue_rub: float = 0
    total_impressions: int = 0
    total_clicks: int = 0
    total_orders: int = 0
    drr_calculated_pct: float | None = None
    roas_calculated: float | None = None
    period_start: date | None = None
    period_end: date | None = None
    daily_comparison: CampaignAutoDailyComparison | None = None
    daily_comparison_unavailable_reason: str | None = None


class ProductAdCampaignBreakdown(BaseModel):
    """One campaign's contribution to a single product's advertising numbers
    — a campaign usually covers many SKUs, so a product's ad tab needs to
    break down by campaign, the mirror image of a campaign's own detail page
    (which breaks down by date only, since it already knows its one
    campaign)."""

    campaign_id: str  # ozon_campaign_id — stable even if AdvertisingCampaign itself was never synced
    campaign_name: str
    campaign_state: str | None
    spend_rub: float
    impressions: int
    clicks: int
    orders: int
    revenue_rub: float
    drr_calculated_pct: float | None
    roas_calculated: float | None


class ProductAdvertisingAutoDailyOut(BaseModel):
    """Same aggregation logic as CampaignAutoDailyDetailOut, but sliced by
    ozon_sku across every campaign that advertised it, instead of by one
    campaign across every SKU it covers. Source: the same auto-collected
    AdvertisingDailyStatistic rows (Ozon Performance API statistics-report
    sync) — no CSV-upload equivalent exists per-product yet (see
    AdvertisingAnalyticsOut.by_campaign via GET /advertising/analytics?
    product_id=... for the CSV-sourced per-product view)."""

    has_data: bool = False
    total_spend_rub: float = 0
    total_revenue_rub: float = 0
    total_impressions: int = 0
    total_clicks: int = 0
    total_orders: int = 0
    drr_calculated_pct: float | None = None
    roas_calculated: float | None = None
    period_start: date | None = None
    period_end: date | None = None
    daily_comparison: CampaignAutoDailyComparison | None = None
    daily_comparison_unavailable_reason: str | None = None
    by_campaign: list[ProductAdCampaignBreakdown] = []


class ProductCampaignDailyRow(BaseModel):
    """One (product, campaign) pair's numbers for a single day — the
    expanded-row detail on the product detail page's "Реклама" tab, same
    auto-collected AdvertisingDailyStatistic source as everything else here,
    just not aggregated across dates this time."""

    date: date
    spend_rub: float
    impressions: int
    clicks: int
    orders: int
    revenue_rub: float
    drr_calculated_pct: float | None
    roas_calculated: float | None


class ProductCampaignDailyListResponse(BaseModel):
    items: list[ProductCampaignDailyRow]
    total: int


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

    # Totals from the SEPARATE, automatically-collected AdvertisingDailyStatistic
    # source (Ozon Performance API) for this same campaign — never merged into
    # the CSV-based totals above (see AdvertisingDailyStatistic's own docstring
    # for why summing the two risks double-counting spend/revenue). Always
    # present (has_data=False when there's nothing collected yet), so the
    # frontend never has to guess whether the field exists.
    auto_daily: CampaignAutoDailyDetailOut = CampaignAutoDailyDetailOut()


class AdvertisingDailyStatisticOut(BaseModel):
    """One row from the automatic Ozon Performance API sync (see
    app.services.advertising_daily_sync_service) — genuinely daily, unlike
    AdvertisingStatisticOut which reflects whatever period a seller's CSV
    upload covers. Deliberately a separate schema/endpoint: summing this
    together with AdvertisingStatisticOut client-side risks double-counting
    spend/revenue if both sources cover the same campaign/period."""

    id: str
    product_id: str | None
    product_name: str | None = None
    campaign_id: str | None
    campaign_name: str | None = None
    ozon_campaign_id: str
    ozon_sku: str
    date: date

    product_price_rub: float | None
    page_type: str | None
    impression_condition: str | None
    impressions: int | None
    clicks: int | None
    ctr_pct_ozon: float | None
    cart_additions: int | None
    avg_bid_rub_ozon: float | None
    spend_rub: float | None
    orders: int | None
    revenue_rub: float | None
    orders_model: int | None
    revenue_model_rub: float | None
    drr_promo_pct_ozon: float | None
    drr_total_pct_ozon: float | None

    model_config = {"from_attributes": True}


class AdvertisingDailyStatisticListResponse(BaseModel):
    items: list[AdvertisingDailyStatisticOut]
    total: int


class AdvertisingAiReviewInsightOut(BaseModel):
    ozon_campaign_id: str
    campaign_name: str
    assessment: Literal["strong", "weak", "neutral"]
    note: str = ""


class AdvertisingAiReviewOut(BaseModel):
    id: str
    period_start: date
    period_end: date
    campaigns_analyzed: int
    overview: str
    insights: list[AdvertisingAiReviewInsightOut]
    anomalies: list[str]
    recommendations: list[str]
    model_used: str | None
    created_at: datetime


class AdvertisingAiReviewListResponse(BaseModel):
    items: list[AdvertisingAiReviewOut]


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
