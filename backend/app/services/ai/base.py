"""AIProvider — the abstraction all AI-backed business logic depends on.

Business logic (review analysis, reply generation) must never import a
concrete provider (Yandex, etc) directly — only this interface, obtained via
app.services.ai.factory.get_ai_provider(). This is what lets a future
GigaChatProvider / OpenAIProvider / ProxyAPIProvider / RouterAIProvider be
added without touching callers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from app.models.store_ai_settings import StoreAISettings
from app.services.ai.schemas import (
    AnalyzeAdvertisingOutcome,
    AnalyzeProductCardOutcome,
    AnalyzeReviewOutcome,
    ConnectionCheckResult,
    GenerateReplyOutcome,
)


class AIProvider(ABC):
    name: str = "base"

    @abstractmethod
    def analyze_review(
        self,
        *,
        product_name: str | None,
        rating: int,
        text: str | None,
        pros: str | None,
        cons: str | None,
        store_settings: StoreAISettings | None,
    ) -> AnalyzeReviewOutcome:
        """Analyze one review and produce the structured JSON result, including
        a first-draft reply_text. Must validate output with Pydantic and attempt
        exactly one repair round-trip on malformed JSON (see schemas.ReviewAnalysisResult)."""
        raise NotImplementedError

    @abstractmethod
    def generate_review_reply(
        self,
        *,
        product_name: str | None,
        rating: int,
        text: str | None,
        pros: str | None,
        cons: str | None,
        store_settings: StoreAISettings | None,
    ) -> GenerateReplyOutcome:
        """Generate (or regenerate) a standalone reply draft."""
        raise NotImplementedError

    @abstractmethod
    def rewrite_review_reply(
        self,
        *,
        existing_reply: str,
        instruction: str,  # "shorter" | "warmer" | "formal" | "regenerate"
        store_settings: StoreAISettings | None,
    ) -> GenerateReplyOutcome:
        raise NotImplementedError

    def analyze_reviews_batch(
        self, reviews: list[dict], store_settings: StoreAISettings | None
    ) -> list[AnalyzeReviewOutcome]:
        """Default implementation just loops analyze_review; providers with a
        genuine batch API may override this."""
        return [
            self.analyze_review(
                product_name=r.get("product_name"),
                rating=r["rating"],
                text=r.get("text"),
                pros=r.get("pros"),
                cons=r.get("cons"),
                store_settings=store_settings,
            )
            for r in reviews
        ]

    @abstractmethod
    def check_connection(self) -> ConnectionCheckResult:
        raise NotImplementedError

    @abstractmethod
    def analyze_advertising_campaigns(
        self,
        *,
        store_name: str | None,
        period_start: date,
        period_end: date,
        campaigns: list[dict],
    ) -> AnalyzeAdvertisingOutcome:
        """Analyze aggregated per-campaign advertising metrics and produce a
        structured overview: which campaigns look strong/weak, anomalies or
        trends worth flagging (e.g. spend rising with flat clicks, a
        dropping CTR), and general recommendations.

        `campaigns` is a list of dicts, one per campaign that has at least
        one day of auto-collected statistics in the period (see
        app.services.advertising_ai_review_service._aggregate_campaigns for
        the exact shape): ozon_campaign_id, name, campaign_type, state,
        daily_budget_rub, total_spend_rub, total_impressions, total_clicks,
        ctr_pct, and a `daily` list of per-day {date, spend_rub,
        impressions, clicks} for trend-spotting.

        Orders, ДРР and ROAS are deliberately NOT part of the input (see the
        service module's own docstring for why) — must not be asked about
        or fabricated in the output."""
        raise NotImplementedError

    @abstractmethod
    def analyze_product_card(
        self,
        *,
        product_name: str | None,
        period_start: date,
        period_end: date,
        ad_daily: list[dict],
        order_daily: list[dict],
        review_summary: dict,
    ) -> AnalyzeProductCardOutcome:
        """Holistic analysis of ONE product's card — combines already-
        computed review findings with the product's own advertising and
        order trend over the period, produced by
        app.services.product_card_ai_review_service (see that module's own
        docstring for why this exists separately from analyze_review /
        analyze_advertising_campaigns).

        ad_daily: list of {date, impressions, clicks, spend_rub} — same
        shape/source as analyze_advertising_campaigns' own `daily`, but
        already filtered to this product's SKU across all its campaigns.
        order_daily: list of {date, ordered_units, buyouts_units} — from
        ProductOrderDailyStatistic, summed across FBO+FBS for this SKU.
        review_summary: {total_reviews, average_rating, low_rating_share,
        top_advantages, top_complaints, product_improvement_ideas,
        card_improvement_ideas} — the SAME aggregates already shown on this
        product's "Рекомендации ИИ"/"Аналитика отзывов" tabs (see
        app.services.analytics_service.compute_review_analytics), not
        recomputed here.

        Cost price, margin, ДРР and ROAS are deliberately NOT part of the
        input (same reasoning as analyze_advertising_campaigns) — must not
        be asked about or fabricated in the output."""
        raise NotImplementedError
