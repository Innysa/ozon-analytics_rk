"""DemoProvider — deterministic, offline stand-in for AI_PROVIDER=demo /
DEMO_MODE. Never calls a real network endpoint. Produces clearly-labelled
example output so demo analysis is never mistaken for a real AI result."""
from __future__ import annotations

from app.models.store_ai_settings import StoreAISettings
from app.services.ai.base import AIProvider
from app.services.ai.schemas import (
    AIUsage,
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
