import pytest
from pydantic import ValidationError

from app.services.ai.demo_provider import DemoProvider
from app.services.ai.schemas import ReviewAnalysisResult


def test_review_analysis_result_rejects_invalid_sentiment():
    with pytest.raises(ValidationError):
        ReviewAnalysisResult(
            sentiment="ecstatic",  # not one of the allowed literals
            category="quality",
            urgency="low",
            reply_needed=True,
        )


def test_review_analysis_result_accepts_valid_payload():
    result = ReviewAnalysisResult(
        sentiment="negative",
        category="quality",
        urgency="high",
        reply_needed=True,
        reply_text="Спасибо за отзыв, приносим извинения.",
        complaints=["товар сломался"],
    )
    assert result.sentiment == "negative"
    assert result.complaints == ["товар сломался"]


def test_demo_provider_never_calls_network_and_labels_output():
    provider = DemoProvider()
    outcome = provider.analyze_review(
        product_name="Тестовый товар", rating=5, text="Отличный товар", pros=None, cons=None, store_settings=None
    )
    assert outcome.success
    assert outcome.result is not None
    assert outcome.result.sentiment == "positive"
    assert "ДЕМО" in outcome.result.reply_text


def test_demo_provider_negative_rating_flags_high_urgency():
    provider = DemoProvider()
    outcome = provider.analyze_review(
        product_name="Тестовый товар", rating=1, text="Всё плохо", pros=None, cons="сломалось", store_settings=None
    )
    assert outcome.result.sentiment == "negative"
    assert outcome.result.urgency == "high"
