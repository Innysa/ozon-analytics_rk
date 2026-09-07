from datetime import date

import pytest
from pydantic import ValidationError

from app.services.ai.demo_provider import DemoProvider
from app.services.ai.schemas import AdvertisingAnalysisResult, ReviewAnalysisResult


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


def test_advertising_analysis_result_rejects_invalid_assessment():
    with pytest.raises(ValidationError):
        AdvertisingAnalysisResult(
            overview="test",
            insights=[{"ozon_campaign_id": "1", "campaign_name": "A", "assessment": "amazing", "note": ""}],
        )


def test_advertising_analysis_result_accepts_valid_payload():
    result = AdvertisingAnalysisResult(
        overview="Расход растёт быстрее кликов у одной кампании.",
        insights=[{"ozon_campaign_id": "1", "campaign_name": "A", "assessment": "weak", "note": "низкий CTR"}],
        anomalies=["резкий рост расхода без роста кликов"],
        recommendations=["снизить ставку у кампании A"],
    )
    assert result.insights[0].assessment == "weak"
    assert result.anomalies == ["резкий рост расхода без роста кликов"]


def test_demo_provider_advertising_analysis_never_calls_network_and_labels_output():
    provider = DemoProvider()
    campaigns = [
        {
            "ozon_campaign_id": "1", "name": "Сильная", "campaign_type": "SKU", "state": "CAMPAIGN_STATE_RUNNING",
            "daily_budget_rub": 500.0, "total_spend_rub": 100.0, "total_impressions": 1000, "total_clicks": 50,
            "ctr_pct": 5.0, "daily": [],
        },
        {
            "ozon_campaign_id": "2", "name": "Слабая", "campaign_type": "SKU", "state": "CAMPAIGN_STATE_RUNNING",
            "daily_budget_rub": 500.0, "total_spend_rub": 200.0, "total_impressions": 2000, "total_clicks": 20,
            "ctr_pct": 1.0, "daily": [],
        },
    ]
    outcome = provider.analyze_advertising_campaigns(
        store_name="Тестовый магазин", period_start=date(2026, 8, 1), period_end=date(2026, 8, 14), campaigns=campaigns
    )
    assert outcome.success
    assert "ДЕМО" in outcome.result.overview
    insights_by_id = {i.ozon_campaign_id: i for i in outcome.result.insights}
    assert insights_by_id["1"].assessment == "strong"
    assert insights_by_id["2"].assessment == "weak"


def test_demo_provider_advertising_analysis_with_no_campaigns_reports_that_plainly():
    provider = DemoProvider()
    outcome = provider.analyze_advertising_campaigns(
        store_name="Тестовый магазин", period_start=date(2026, 8, 1), period_end=date(2026, 8, 14), campaigns=[]
    )
    assert outcome.success
    assert outcome.result.insights == []
    assert "Нет кампаний" in outcome.result.overview
