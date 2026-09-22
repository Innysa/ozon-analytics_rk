from typing import Literal

from pydantic import BaseModel, Field


class ReviewAnalysisResult(BaseModel):
    """The structured JSON contract every AIProvider must return for a review.
    Matches the schema required by the product spec exactly."""

    sentiment: Literal["positive", "neutral", "negative"]
    category: Literal[
        "quality", "size", "assembly", "delivery", "packaging", "color",
        "price", "missing_parts", "usability", "other",
    ]
    urgency: Literal["low", "medium", "high"]
    reply_needed: bool
    reply_text: str = ""
    advantages: list[str] = Field(default_factory=list)
    complaints: list[str] = Field(default_factory=list)
    product_improvements: list[str] = Field(default_factory=list)
    card_improvements: list[str] = Field(default_factory=list)
    infographic_ideas: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)


class AIUsage(BaseModel):
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    estimated_cost_rub: float | None = None


class AnalyzeReviewOutcome(BaseModel):
    result: ReviewAnalysisResult | None
    usage: AIUsage
    success: bool
    error_message: str | None = None


class GenerateReplyOutcome(BaseModel):
    reply_text: str | None
    usage: AIUsage
    success: bool
    error_message: str | None = None


class ConnectionCheckResult(BaseModel):
    ok: bool
    message: str


class AdvertisingCampaignInsight(BaseModel):
    """One campaign's place in the overall advertising-review verdict."""

    ozon_campaign_id: str
    campaign_name: str
    assessment: Literal["strong", "weak", "neutral"]
    note: str = ""


class AdvertisingAnalysisResult(BaseModel):
    """The structured JSON contract every AIProvider must return for
    analyze_advertising_campaigns(). Deliberately has no ДРР/ROAS/orders
    field — the input never includes them (see
    app.services.advertising_ai_review_service's module docstring), so
    there is nothing for the model to (mis)report here either."""

    overview: str
    insights: list[AdvertisingCampaignInsight] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class AnalyzeAdvertisingOutcome(BaseModel):
    result: AdvertisingAnalysisResult | None
    usage: AIUsage
    success: bool
    error_message: str | None = None


class ProductCardAnalysisResult(BaseModel):
    """The structured JSON contract every AIProvider must return for
    analyze_product_card() — a holistic view of ONE product combining
    review findings with its advertising and order trends over the period.
    trend_observations describes what the DATA shows (e.g. "показы выросли
    на 20%, заказы упали на 15%") — hypotheses is for anything that LINKS
    that trend to a review finding or another data point without being
    directly provable from the numbers alone (e.g. "падение заказов может
    быть связано с жалобами на упаковку в отзывах") and must read as a
    hypothesis, not a fact (same discipline as ReviewAnalysisResult.
    hypotheses)."""

    overview: str
    trend_observations: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class AnalyzeProductCardOutcome(BaseModel):
    result: ProductCardAnalysisResult | None
    usage: AIUsage
    success: bool
    error_message: str | None = None
