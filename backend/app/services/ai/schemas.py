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
