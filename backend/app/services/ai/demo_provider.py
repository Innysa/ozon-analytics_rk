"""DemoProvider — deterministic, offline stand-in for AI_PROVIDER=demo /
DEMO_MODE. Never calls a real network endpoint. Produces clearly-labelled
example output so demo analysis is never mistaken for a real AI result."""
from __future__ import annotations

from datetime import date

from app.models.store_ai_settings import StoreAISettings
from app.services.ai.base import AIProvider
from app.services.ai.schemas import (
    AdvertisingAnalysisResult,
    AdvertisingCampaignInsight,
    AIUsage,
    AnalyzeAdvertisingOutcome,
    AnalyzeReviewOutcome,
    ConnectionCheckResult,
    GenerateReplyOutcome,
    ReviewAnalysisResult,
)

_DEMO_USAGE = AIUsage(model="demo", prompt_tokens=0, completion_tokens=0, latency_ms=0, estimated_cost_rub=0)


class DemoProvider(AIProvider):
    name = "demo"

    def analyze_review(
        self, *, product_name, rating, text, pros, cons, store_settings: StoreAISettings | None
    ) -> AnalyzeReviewOutcome:
        sentiment = "positive" if rating >= 4 else "negative" if rating <= 2 else "neutral"
        if sentiment == "positive":
            reply = "[ДЕМО] Спасибо за отзыв! Рады, что товар вам подошёл."
            category = "usability"
        elif sentiment == "negative":
            reply = "[ДЕМО] Нам жаль, что вы столкнулись с проблемой. Пожалуйста, обратитесь в поддержку для решения вопроса."
            category = "quality"
        else:
            reply = "[ДЕМО] Спасибо за обратную связь, учтём её в работе."
            category = "other"

        result = ReviewAnalysisResult(
            sentiment=sentiment,
            category=category,
            urgency="high" if sentiment == "negative" else "low",
            reply_needed=True,
            reply_text=reply,
            advantages=["[демо-пример] удобство использования"] if sentiment == "positive" else [],
            complaints=["[демо-пример] нарекание на качество"] if sentiment == "negative" else [],
            product_improvements=[],
            card_improvements=[],
            hypotheses=["[демо-пример] требует проверки человеком"],
        )
        return AnalyzeReviewOutcome(result=result, usage=_DEMO_USAGE, success=True)

    def generate_review_reply(self, *, product_name, rating, text, pros, cons, store_settings) -> GenerateReplyOutcome:
        outcome = self.analyze_review(
            product_name=product_name, rating=rating, text=text, pros=pros, cons=cons, store_settings=store_settings
        )
        return GenerateReplyOutcome(reply_text=outcome.result.reply_text, usage=_DEMO_USAGE, success=True)

    def rewrite_review_reply(self, *, existing_reply, instruction, store_settings) -> GenerateReplyOutcome:
        suffix = {"shorter": " (короче)", "warmer": " (теплее)", "formal": " (официальнее)"}.get(instruction, " (заново)")
        return GenerateReplyOutcome(reply_text=f"[ДЕМО]{suffix} {existing_reply}", usage=_DEMO_USAGE, success=True)

    def check_connection(self) -> ConnectionCheckResult:
        return ConnectionCheckResult(ok=True, message="Демонстрационный провайдер активен, обращения к сети не выполняются")

    def analyze_advertising_campaigns(
        self,
        *,
        store_name: str | None,
        period_start: date,
        period_end: date,
        campaigns: list[dict],
    ) -> AnalyzeAdvertisingOutcome:
        if not campaigns:
            result = AdvertisingAnalysisResult(overview="[ДЕМО] Нет кампаний с данными за этот период.")
            return AnalyzeAdvertisingOutcome(result=result, usage=_DEMO_USAGE, success=True)

        # Deterministic, not a real analysis: the campaign with the highest
        # CTR is "strong", the lowest is "weak" (ties keep list order), the
        # rest are "neutral" — just enough structure to demo the UI without
        # ever calling out to a real model.
        with_ctr = [c for c in campaigns if c.get("ctr_pct") is not None]
        best_id = max(with_ctr, key=lambda c: c["ctr_pct"])["ozon_campaign_id"] if with_ctr else None
        worst_id = min(with_ctr, key=lambda c: c["ctr_pct"])["ozon_campaign_id"] if len(with_ctr) > 1 else None

        insights = []
        for c in campaigns:
            if c["ozon_campaign_id"] == best_id and best_id != worst_id:
                assessment, note = "strong", "[демо-пример] лучший CTR среди кампаний за период"
            elif c["ozon_campaign_id"] == worst_id:
                assessment, note = "weak", "[демо-пример] худший CTR среди кампаний за период"
            else:
                assessment, note = "neutral", "[демо-пример] без выраженных отклонений"
            insights.append(
                AdvertisingCampaignInsight(
                    ozon_campaign_id=c["ozon_campaign_id"], campaign_name=c["name"], assessment=assessment, note=note
                )
            )

        result = AdvertisingAnalysisResult(
            overview=f"[ДЕМО] Проанализировано кампаний: {len(campaigns)} за период {period_start.isoformat()}—{period_end.isoformat()}.",
            insights=insights,
            anomalies=["[демо-пример] возможен резкий рост расхода без роста кликов у одной из кампаний"],
            recommendations=["[демо-пример] проверить ставки у слабой кампании"],
        )
        return AnalyzeAdvertisingOutcome(result=result, usage=_DEMO_USAGE, success=True)
