"""Review analytics: aggregates only over data that actually exists in the DB.

Nothing here invents numbers — if there are no reviews (or no AI analyses),
`has_data` is False and every other field stays empty/None. Every aggregated
"top complaint"/"top advantage" keeps the list of source review IDs so the UI
can render the required "Показать отзывы-основания" link, and outputs coming
from AI text fields (product_improvements/card_improvements/hypotheses) are
kept clearly separate from calculated counts.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.review import Review, ReviewStatus
from app.models.review_ai_analysis import ReviewAIAnalysis
from app.schemas.analytics import NamedCount, RatingBucket, ReviewAnalyticsOut


def _aggregate_named(pairs: list[tuple[str, str]]) -> list[NamedCount]:
    """pairs: list of (text, review_id). Groups by exact text match."""
    buckets: dict[str, list[str]] = defaultdict(list)
    for text, review_id in pairs:
        text = text.strip()
        if not text:
            continue
        buckets[text].append(review_id)
    ranked = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)
    return [NamedCount(label=label, count=len(ids), review_ids=ids) for label, ids in ranked[:15]]


def compute_review_analytics(db: Session, *, store_id: str, product_id: str | None = None) -> ReviewAnalyticsOut:
    stmt = select(Review).where(Review.store_id == store_id)
    if product_id:
        stmt = stmt.where(Review.product_id == product_id)
    reviews = db.scalars(stmt).all()

    if not reviews:
        return ReviewAnalyticsOut(has_data=False)

    total = len(reviews)
    avg_rating = sum(r.rating for r in reviews) / total
    low_share = sum(1 for r in reviews if r.rating <= 3) / total
    without_reply = sum(
        1 for r in reviews if not r.existing_seller_reply and r.status != ReviewStatus.PUBLISHED
    )

    distribution = defaultdict(int)
    for r in reviews:
        distribution[r.rating] += 1
    rating_distribution = [RatingBucket(rating=i, count=distribution.get(i, 0)) for i in range(1, 6)]

    analyses = db.scalars(
        select(ReviewAIAnalysis).where(ReviewAIAnalysis.review_id.in_([r.id for r in reviews]))
    ).all()

    advantage_pairs: list[tuple[str, str]] = []
    complaint_pairs: list[tuple[str, str]] = []
    product_improvement_pairs: list[tuple[str, str]] = []
    card_improvement_pairs: list[tuple[str, str]] = []
    infographic_pairs: list[tuple[str, str]] = []

    for a in analyses:
        for item in json.loads(a.advantages_json or "[]"):
            advantage_pairs.append((item, a.review_id))
        for item in json.loads(a.complaints_json or "[]"):
            complaint_pairs.append((item, a.review_id))
        for item in json.loads(a.product_improvements_json or "[]"):
            product_improvement_pairs.append((item, a.review_id))
        for item in json.loads(a.card_improvements_json or "[]"):
            card_improvement_pairs.append((item, a.review_id))
            infographic_pairs.append((item, a.review_id))

    # Products with rising negativity: average rating of the last 30 days vs the 30 days before that.
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=30)
    prior_cutoff = now - timedelta(days=60)
    by_product_recent: dict[str, list[int]] = defaultdict(list)
    by_product_prior: dict[str, list[int]] = defaultdict(list)
    for r in reviews:
        if not r.product_id or not r.published_at:
            continue
        published_at = r.published_at if r.published_at.tzinfo else r.published_at.replace(tzinfo=timezone.utc)
        if published_at >= recent_cutoff:
            by_product_recent[r.product_id].append(r.rating)
        elif prior_cutoff <= published_at < recent_cutoff:
            by_product_prior[r.product_id].append(r.rating)

    rising_negativity = []
    for product_id_key, recent_ratings in by_product_recent.items():
        prior_ratings = by_product_prior.get(product_id_key)
        if not prior_ratings:
            continue
        if (sum(recent_ratings) / len(recent_ratings)) < (sum(prior_ratings) / len(prior_ratings)):
            rising_negativity.append(product_id_key)

    return ReviewAnalyticsOut(
        has_data=True,
        total_reviews=total,
        average_rating=round(avg_rating, 2),
        low_rating_share=round(low_share, 3),
        reviews_without_reply=without_reply,
        rating_distribution=rating_distribution,
        top_advantages=_aggregate_named(advantage_pairs),
        top_complaints=_aggregate_named(complaint_pairs),
        products_with_rising_negativity=rising_negativity,
        card_improvement_ideas=_aggregate_named(card_improvement_pairs),
        product_improvement_ideas=_aggregate_named(product_improvement_pairs),
        infographic_ideas=_aggregate_named(infographic_pairs),
    )
