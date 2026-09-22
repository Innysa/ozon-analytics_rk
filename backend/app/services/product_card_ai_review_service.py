"""Generates a holistic AI-written analysis of ONE product's card, combining
three ALREADY-COLLECTED sources — no new Ozon call is made here:

  - Advertising (AdvertisingDailyStatistic filtered by this product's
    ozon_sku, across every campaign it appeared in) — same source as the
    product page's own "Реклама" tab and analyze_advertising_campaigns.
  - Orders (ProductOrderDailyStatistic, summed across FBO+FBS for this
    SKU) — same source as "РНП Товары"/the product page's "Продажи" tab.
  - Reviews (app.services.analytics_service.compute_review_analytics,
    filtered to this product) — the SAME aggregates already shown on this
    product's "Аналитика отзывов"/"Рекомендации ИИ" tabs, not recomputed
    here.

ADDED 2026-09-22, requested by the store owner: the existing "Рекомендации
ИИ" tab only ever analyzed review text. She wanted a full picture — is
advertising performance rising or falling, are orders rising or falling,
and (as a clearly-labeled hypothesis, never a stated fact) whether that
lines up with what reviews say.

Deliberately MANUAL-TRIGGER ONLY, no daily scheduler (unlike
advertising_ai_review_service, which auto-runs once a day for the whole
store): running this automatically for every product in a store would be
one AI call per product per day for data that often doesn't change
meaningfully day to day — a real cost/volume difference from the
store-wide advertising review, which is exactly one call per store per
day regardless of how many campaigns it covers. If a store's owner wants
this refreshed regularly, they click the button on the products that
matter to them.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
from app.models.product import Product
from app.models.product_card_ai_review import ProductCardAiReview
from app.models.product_order_daily_statistic import ProductOrderDailyStatistic
from app.services.ai.base import AIProvider
from app.services.analytics_service import compute_review_analytics


@dataclass
class ProductCardAiReviewOutcome:
    saved: bool = False
    reviews_considered: int = 0
    errors: list[str] = field(default_factory=list)


def _aggregate_ad_daily(db: Session, *, store_id: str, ozon_sku: str, date_from: date, date_to: date) -> list[dict]:
    """Same principle as advertising_ai_review_service._aggregate_campaigns
    — sums across every campaign that advertised this SKU on a given day,
    since the model reasons about the PRODUCT's own ad performance, not
    any one campaign."""
    rows = (
        db.query(AdvertisingDailyStatistic)
        .filter(
            AdvertisingDailyStatistic.store_id == store_id,
            AdvertisingDailyStatistic.ozon_sku == ozon_sku,
            AdvertisingDailyStatistic.date >= date_from,
            AdvertisingDailyStatistic.date <= date_to,
        )
        .all()
    )
    by_day: dict[date, dict] = defaultdict(lambda: {"impressions": 0, "clicks": 0, "spend_rub": 0.0})
    for r in rows:
        acc = by_day[r.date]
        acc["impressions"] += r.impressions or 0
        acc["clicks"] += r.clicks or 0
        acc["spend_rub"] += float(r.spend_rub or 0)
    return [
        {"date": d.isoformat(), "impressions": v["impressions"], "clicks": v["clicks"], "spend_rub": round(v["spend_rub"], 2)}
        for d, v in sorted(by_day.items())
    ]


def _aggregate_order_daily(db: Session, *, store_id: str, ozon_sku: str, date_from: date, date_to: date) -> list[dict]:
    rows = (
        db.query(ProductOrderDailyStatistic)
        .filter(
            ProductOrderDailyStatistic.store_id == store_id,
            ProductOrderDailyStatistic.ozon_sku == ozon_sku,
            ProductOrderDailyStatistic.date >= date_from,
            ProductOrderDailyStatistic.date <= date_to,
        )
        .all()
    )
    by_day: dict[date, dict] = defaultdict(lambda: {"ordered_units": 0, "buyouts_units": 0})
    for r in rows:
        acc = by_day[r.date]
        acc["ordered_units"] += r.ordered_units or 0
        acc["buyouts_units"] += r.delivered_units or 0
    return [
        {"date": d.isoformat(), "ordered_units": v["ordered_units"], "buyouts_units": v["buyouts_units"]}
        for d, v in sorted(by_day.items())
    ]


def _review_summary_dict(db: Session, *, store_id: str, product_id: str) -> dict:
    analytics = compute_review_analytics(db, store_id=store_id, product_id=product_id)
    return {
        "total_reviews": analytics.total_reviews,
        "average_rating": analytics.average_rating,
        "low_rating_share": analytics.low_rating_share,
        "top_advantages": [i.label for i in analytics.top_advantages],
        "top_complaints": [i.label for i in analytics.top_complaints],
        "product_improvement_ideas": [i.label for i in analytics.product_improvement_ideas],
        "card_improvement_ideas": [i.label for i in analytics.card_improvement_ideas],
    }


def generate_product_card_ai_review(
    db: Session,
    *,
    store_id: str,
    product_id: str,
    ai_provider: AIProvider,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ProductCardAiReviewOutcome:
    settings = get_settings()
    outcome = ProductCardAiReviewOutcome()

    product = db.get(Product, product_id)
    if not product or product.store_id != store_id:
        outcome.errors.append("Товар не найден")
        return outcome

    today = datetime.now(timezone.utc).date()
    # "Yesterday", not "today" — same caution as advertising_ai_review_
    # service's own resolved_date_to: today's daily-statistic rows are
    # likely still incomplete.
    resolved_date_to = date_to or (today - timedelta(days=1))
    resolved_date_from = date_from or (resolved_date_to - timedelta(days=settings.PRODUCT_CARD_AI_REVIEW_LOOKBACK_DAYS - 1))

    ad_daily = _aggregate_ad_daily(db, store_id=store_id, ozon_sku=product.ozon_sku, date_from=resolved_date_from, date_to=resolved_date_to)
    order_daily = _aggregate_order_daily(db, store_id=store_id, ozon_sku=product.ozon_sku, date_from=resolved_date_from, date_to=resolved_date_to)
    review_summary = _review_summary_dict(db, store_id=store_id, product_id=product_id)

    if not ad_daily and not order_daily and review_summary["total_reviews"] == 0:
        outcome.errors.append(
            f"Нет данных для анализа за период {resolved_date_from.isoformat()}..{resolved_date_to.isoformat()} — "
            f"нет ни рекламы, ни заказов, ни отзывов по этому товару."
        )
        return outcome

    outcome.reviews_considered = review_summary["total_reviews"]

    ai_outcome = ai_provider.analyze_product_card(
        product_name=product.name,
        period_start=resolved_date_from,
        period_end=resolved_date_to,
        ad_daily=ad_daily,
        order_daily=order_daily,
        review_summary=review_summary,
    )
    if not ai_outcome.success or ai_outcome.result is None:
        outcome.errors.append(f"AI-анализ не удался: {ai_outcome.error_message or 'неизвестная ошибка'}")
        return outcome

    result = ai_outcome.result
    existing = (
        db.query(ProductCardAiReview)
        .filter(
            ProductCardAiReview.store_id == store_id,
            ProductCardAiReview.product_id == product_id,
            ProductCardAiReview.period_start == resolved_date_from,
            ProductCardAiReview.period_end == resolved_date_to,
        )
        .first()
    )
    review = existing or ProductCardAiReview(
        store_id=store_id, product_id=product_id, period_start=resolved_date_from, period_end=resolved_date_to,
    )
    review.overview = result.overview
    review.trend_observations_json = json.dumps(result.trend_observations, ensure_ascii=False)
    review.hypotheses_json = json.dumps(result.hypotheses, ensure_ascii=False)
    review.recommendations_json = json.dumps(result.recommendations, ensure_ascii=False)
    review.reviews_considered = review_summary["total_reviews"]
    review.model_used = ai_outcome.usage.model
    if existing is None:
        db.add(review)
    db.commit()

    outcome.saved = True
    return outcome
