"""Tests for the full-card AI analysis service (reviews + advertising +
order trend combined for ONE product) — aggregation of per-SKU daily
advertising/order data, review-summary shape, and the generate/save/
update-in-place flow. Mirrors test_advertising_ai_review_service.py's own
conventions; uses a fake, duck-typed AIProvider."""
import json
from datetime import date

from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
from app.models.product import Product
from app.models.product_card_ai_review import ProductCardAiReview
from app.models.product_order_daily_statistic import ProductOrderDailyStatistic
from app.models.review import Review, ReviewSource, ReviewStatus
from app.services.ai.schemas import AIUsage, AnalyzeProductCardOutcome, ProductCardAnalysisResult
from app.services.product_card_ai_review_service import (
    _aggregate_ad_daily,
    _aggregate_order_daily,
    generate_product_card_ai_review,
)


class FakeAIProvider:
    def __init__(self, outcome: AnalyzeProductCardOutcome | None = None):
        self.calls: list[dict] = []
        self._outcome = outcome or AnalyzeProductCardOutcome(
            result=ProductCardAnalysisResult(overview="Тестовый анализ"), usage=AIUsage(model="fake"), success=True
        )

    def analyze_product_card(self, *, product_name, period_start, period_end, ad_daily, order_daily, review_summary):
        self.calls.append({
            "product_name": product_name, "period_start": period_start, "period_end": period_end,
            "ad_daily": ad_daily, "order_daily": order_daily, "review_summary": review_summary,
        })
        return self._outcome


def _make_product(db_session, store_id: str, *, sku: str = "555", name: str = "Товар"):
    product = Product(store_id=store_id, ozon_sku=sku, name=name)
    db_session.add(product)
    db_session.flush()
    return product


def test_aggregate_ad_daily_sums_across_campaigns_for_the_sku(db_session, two_stores_with_users):
    d = two_stores_with_users
    db_session.add_all([
        AdvertisingDailyStatistic(store_id=d["store_a"].id, ozon_campaign_id="111", ozon_sku="555", date=date(2026, 8, 1), spend_rub=100, impressions=1000, clicks=50, source="ozon_performance_api"),
        AdvertisingDailyStatistic(store_id=d["store_a"].id, ozon_campaign_id="222", ozon_sku="555", date=date(2026, 8, 1), spend_rub=50, impressions=500, clicks=10, source="ozon_performance_api"),
        AdvertisingDailyStatistic(store_id=d["store_a"].id, ozon_campaign_id="111", ozon_sku="999", date=date(2026, 8, 1), spend_rub=999, impressions=9999, clicks=999, source="ozon_performance_api"),
    ])
    db_session.commit()

    rows = _aggregate_ad_daily(db_session, store_id=d["store_a"].id, ozon_sku="555", date_from=date(2026, 8, 1), date_to=date(2026, 8, 1))

    assert rows == [{"date": "2026-08-01", "impressions": 1500, "clicks": 60, "spend_rub": 150.0}]


def test_aggregate_order_daily_sums_across_fbo_and_fbs(db_session, two_stores_with_users):
    d = two_stores_with_users
    db_session.add_all([
        ProductOrderDailyStatistic(store_id=d["store_a"].id, ozon_sku="555", date=date(2026, 8, 1), delivery_schema="FBO", ordered_units=10, ordered_sum_rub=1000, ordered_sum_discounted_rub=1000, delivered_units=5, delivered_sum_rub=500, cancelled_units=0, cancelled_sum_rub=0, unfinished_units=5, commission_rub=0, source="ozon_seller_api"),
        ProductOrderDailyStatistic(store_id=d["store_a"].id, ozon_sku="555", date=date(2026, 8, 1), delivery_schema="FBS", ordered_units=3, ordered_sum_rub=300, ordered_sum_discounted_rub=300, delivered_units=2, delivered_sum_rub=200, cancelled_units=0, cancelled_sum_rub=0, unfinished_units=1, commission_rub=0, source="ozon_seller_api"),
    ])
    db_session.commit()

    rows = _aggregate_order_daily(db_session, store_id=d["store_a"].id, ozon_sku="555", date_from=date(2026, 8, 1), date_to=date(2026, 8, 1))

    assert rows == [{"date": "2026-08-01", "ordered_units": 13, "buyouts_units": 7}]


def test_generate_with_no_data_at_all_reports_a_clear_error(db_session, two_stores_with_users):
    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id)
    db_session.commit()
    ai = FakeAIProvider()

    outcome = generate_product_card_ai_review(
        db_session, store_id=d["store_a"].id, product_id=product.id, ai_provider=ai,
        date_from=date(2026, 8, 1), date_to=date(2026, 8, 1),
    )

    assert outcome.saved is False
    assert any("Нет данных" in e for e in outcome.errors)
    assert ai.calls == []
    assert db_session.query(ProductCardAiReview).count() == 0


def test_generate_with_only_reviews_still_analyzes(db_session, two_stores_with_users):
    """Even with zero ad/order data, reviews alone are enough to run —
    matches the existing "Рекомендации ИИ" tab's own review-only baseline."""
    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id)
    db_session.add(Review(
        store_id=d["store_a"].id, product_id=product.id, ozon_review_id="r1", source=ReviewSource.OZON_API,
        rating=5, status=ReviewStatus.NEW,
    ))
    db_session.commit()
    ai = FakeAIProvider()

    outcome = generate_product_card_ai_review(
        db_session, store_id=d["store_a"].id, product_id=product.id, ai_provider=ai,
        date_from=date(2026, 8, 1), date_to=date(2026, 8, 1),
    )

    assert outcome.saved is True
    assert outcome.reviews_considered == 1
    assert len(ai.calls) == 1
    assert ai.calls[0]["review_summary"]["total_reviews"] == 1
    assert ai.calls[0]["ad_daily"] == []
    assert ai.calls[0]["order_daily"] == []


def test_generate_saves_full_result_and_passes_product_name(db_session, two_stores_with_users):
    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id, name="Стеллаж для игрушек")
    db_session.add(AdvertisingDailyStatistic(
        store_id=d["store_a"].id, ozon_campaign_id="111", ozon_sku="555", date=date(2026, 8, 1),
        spend_rub=100, impressions=1000, clicks=50, source="ozon_performance_api",
    ))
    db_session.commit()

    ai = FakeAIProvider(AnalyzeProductCardOutcome(
        result=ProductCardAnalysisResult(
            overview="Реклама растёт, заказов пока мало.",
            trend_observations=["показы выросли"],
            hypotheses=["возможна связь с жалобами в отзывах"],
            recommendations=["проверить карточку"],
        ),
        usage=AIUsage(model="fake-model"), success=True,
    ))

    outcome = generate_product_card_ai_review(
        db_session, store_id=d["store_a"].id, product_id=product.id, ai_provider=ai,
        date_from=date(2026, 8, 1), date_to=date(2026, 8, 1),
    )

    assert outcome.saved is True
    assert ai.calls[0]["product_name"] == "Стеллаж для игрушек"

    row = db_session.query(ProductCardAiReview).filter(ProductCardAiReview.product_id == product.id).one()
    assert row.overview == "Реклама растёт, заказов пока мало."
    assert json.loads(row.trend_observations_json) == ["показы выросли"]
    assert json.loads(row.hypotheses_json) == ["возможна связь с жалобами в отзывах"]
    assert row.model_used == "fake-model"


def test_generate_for_same_period_updates_in_place_not_duplicated(db_session, two_stores_with_users):
    d = two_stores_with_users
    product = _make_product(db_session, d["store_a"].id)
    db_session.add(AdvertisingDailyStatistic(
        store_id=d["store_a"].id, ozon_campaign_id="111", ozon_sku="555", date=date(2026, 8, 1),
        spend_rub=100, impressions=1000, clicks=50, source="ozon_performance_api",
    ))
    db_session.commit()

    ai1 = FakeAIProvider(AnalyzeProductCardOutcome(result=ProductCardAnalysisResult(overview="Версия 1"), usage=AIUsage(model="fake"), success=True))
    generate_product_card_ai_review(db_session, store_id=d["store_a"].id, product_id=product.id, ai_provider=ai1, date_from=date(2026, 8, 1), date_to=date(2026, 8, 1))

    ai2 = FakeAIProvider(AnalyzeProductCardOutcome(result=ProductCardAnalysisResult(overview="Версия 2"), usage=AIUsage(model="fake"), success=True))
    generate_product_card_ai_review(db_session, store_id=d["store_a"].id, product_id=product.id, ai_provider=ai2, date_from=date(2026, 8, 1), date_to=date(2026, 8, 1))

    rows = db_session.query(ProductCardAiReview).filter(ProductCardAiReview.product_id == product.id).all()
    assert len(rows) == 1
    assert rows[0].overview == "Версия 2"


def test_generate_for_unknown_product_reports_a_clear_error(db_session, two_stores_with_users):
    d = two_stores_with_users
    ai = FakeAIProvider()

    outcome = generate_product_card_ai_review(
        db_session, store_id=d["store_a"].id, product_id="does-not-exist", ai_provider=ai,
        date_from=date(2026, 8, 1), date_to=date(2026, 8, 1),
    )

    assert outcome.saved is False
    assert any("не найден" in e for e in outcome.errors)
    assert ai.calls == []


def test_generate_rejects_product_from_another_store(db_session, two_stores_with_users):
    d = two_stores_with_users
    other_product = _make_product(db_session, d["store_b"].id)
    db_session.commit()
    ai = FakeAIProvider()

    outcome = generate_product_card_ai_review(
        db_session, store_id=d["store_a"].id, product_id=other_product.id, ai_provider=ai,
        date_from=date(2026, 8, 1), date_to=date(2026, 8, 1),
    )

    assert outcome.saved is False
    assert any("не найден" in e for e in outcome.errors)
    assert ai.calls == []
