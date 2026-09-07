"""Generates a periodic AI-written overview of a store's Ozon advertising
campaigns, using the same AIProvider (app.services.ai) already used for
review analysis — see app.services.advertising_ai_review_scheduler for the
automatic daily run, and POST .../advertising/ai-review/generate for the
manual trigger.

Input data is exclusively AdvertisingCampaign (metadata) and
AdvertisingDailyStatistic (spend/impressions/clicks/CTR, auto-collected via
Ozon Performance API — see app.services.advertising_daily_sync_service).
Orders, ДРР, and ROAS are deliberately NOT included yet: those live in the
separate, CSV-upload-based AdvertisingStatistic table (see that model's own
docstring for why the two sources aren't merged), and wiring that in is a
follow-up step once the XLSX-report connection is itself more structured —
asking the AI to reason about profitability from spend/clicks alone would
mean fabricating a ДРР/ROAS figure it doesn't actually have.

Storage: one AdvertisingAiReview row per (store, period_start, period_end),
keyed the same way as SearchQueryStatistic — a daily scheduled run with a
sliding period_end naturally builds history instead of overwriting it,
while re-running for the exact same period (e.g. a manual re-trigger the
same day) updates the existing row in place.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.advertising_ai_review import AdvertisingAiReview
from app.models.advertising_campaign import AdvertisingCampaign
from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
from app.models.store import Store
from app.services.ai.base import AIProvider


@dataclass
class AdvertisingAiReviewOutcome:
    campaigns_analyzed: int = 0
    saved: bool = False
    errors: list[str] = field(default_factory=list)


def _aggregate_campaigns(db: Session, store_id: str, date_from: date, date_to: date) -> list[dict]:
    """Per-campaign totals plus a day-by-day series, for every campaign that
    has at least one day of auto-collected AdvertisingDailyStatistic in the
    window — a campaign never synced (or with no traffic at all in this
    window) is simply not included, rather than fabricating a zeroed entry
    for it. Sorted by total spend, highest first, so the prompt's most
    consequential campaigns come first."""
    rows = (
        db.query(AdvertisingDailyStatistic)
        .filter(
            AdvertisingDailyStatistic.store_id == store_id,
            AdvertisingDailyStatistic.date >= date_from,
            AdvertisingDailyStatistic.date <= date_to,
            AdvertisingDailyStatistic.ozon_campaign_id.isnot(None),
        )
        .all()
    )
    if not rows:
        return []

    by_campaign_day: dict[tuple[str, date], dict] = {}
    for r in rows:
        key = (r.ozon_campaign_id, r.date)
        acc = by_campaign_day.setdefault(key, {"spend": 0.0, "impressions": 0, "clicks": 0})
        acc["spend"] += float(r.spend_rub or 0)
        acc["impressions"] += r.impressions or 0
        acc["clicks"] += r.clicks or 0

    campaigns_by_ozon_id = {
        c.ozon_campaign_id: c
        for c in db.query(AdvertisingCampaign).filter(AdvertisingCampaign.store_id == store_id).all()
    }

    per_campaign: dict[str, dict] = {}
    for (ozon_campaign_id, day), acc in by_campaign_day.items():
        entry = per_campaign.setdefault(
            ozon_campaign_id, {"daily": [], "total_spend": 0.0, "total_impressions": 0, "total_clicks": 0}
        )
        entry["daily"].append(
            {"date": day.isoformat(), "spend_rub": round(acc["spend"], 2), "impressions": acc["impressions"], "clicks": acc["clicks"]}
        )
        entry["total_spend"] += acc["spend"]
        entry["total_impressions"] += acc["impressions"]
        entry["total_clicks"] += acc["clicks"]

    result: list[dict] = []
    for ozon_campaign_id, entry in per_campaign.items():
        campaign = campaigns_by_ozon_id.get(ozon_campaign_id)
        entry["daily"].sort(key=lambda d: d["date"])
        ctr = (entry["total_clicks"] / entry["total_impressions"] * 100) if entry["total_impressions"] else None
        result.append(
            {
                "ozon_campaign_id": ozon_campaign_id,
                "name": campaign.name if campaign and campaign.name else ozon_campaign_id,
                "campaign_type": campaign.campaign_type if campaign else None,
                "state": campaign.state if campaign else None,
                "daily_budget_rub": float(campaign.daily_budget_rub) if campaign and campaign.daily_budget_rub is not None else None,
                "total_spend_rub": round(entry["total_spend"], 2),
                "total_impressions": entry["total_impressions"],
                "total_clicks": entry["total_clicks"],
                "ctr_pct": round(ctr, 2) if ctr is not None else None,
                "daily": entry["daily"],
            }
        )
    result.sort(key=lambda c: c["total_spend_rub"], reverse=True)
    return result


def generate_advertising_ai_review(
    db: Session,
    *,
    store_id: str,
    ai_provider: AIProvider,
    date_from: date | None = None,
    date_to: date | None = None,
) -> AdvertisingAiReviewOutcome:
    """Runs one AI-review generation for one store. The caller owns SyncRun
    bookkeeping and the session's overall lifecycle (see
    app.services.advertising_ai_review_scheduler)."""
    settings = get_settings()
    outcome = AdvertisingAiReviewOutcome()

    today = datetime.now(timezone.utc).date()
    # "Yesterday", not "today": today's own daily-statistics rows are likely
    # incomplete (the ad-stats sync itself runs once a day and may not have
    # captured a full day yet) — same "don't analyze a still-filling-in day"
    # caution as the search-query-stats sync's own date_to handling.
    resolved_date_to = date_to or (today - timedelta(days=1))
    resolved_date_from = date_from or (resolved_date_to - timedelta(days=settings.ADVERTISING_AI_REVIEW_LOOKBACK_DAYS - 1))

    campaigns = _aggregate_campaigns(db, store_id, resolved_date_from, resolved_date_to)
    if not campaigns:
        outcome.errors.append(
            f"Нет автоматически собранной статистики рекламы за период {resolved_date_from.isoformat()}"
            f"..{resolved_date_to.isoformat()} — сначала запустите автосбор статистики рекламы "
            f"(кнопка «Обновить статистику (авто)» на странице «Реклама»)."
        )
        return outcome
    outcome.campaigns_analyzed = len(campaigns)

    store = db.get(Store, store_id)
    ai_outcome = ai_provider.analyze_advertising_campaigns(
        store_name=store.name if store else None,
        period_start=resolved_date_from,
        period_end=resolved_date_to,
        campaigns=campaigns,
    )
    if not ai_outcome.success or ai_outcome.result is None:
        outcome.errors.append(f"AI-обзор не удался: {ai_outcome.error_message or 'неизвестная ошибка'}")
        return outcome

    result = ai_outcome.result
    existing = (
        db.query(AdvertisingAiReview)
        .filter(
            AdvertisingAiReview.store_id == store_id,
            AdvertisingAiReview.period_start == resolved_date_from,
            AdvertisingAiReview.period_end == resolved_date_to,
        )
        .first()
    )
    review = existing or AdvertisingAiReview(store_id=store_id, period_start=resolved_date_from, period_end=resolved_date_to)
    review.campaigns_analyzed = len(campaigns)
    review.overview = result.overview
    review.insights_json = json.dumps([i.model_dump() for i in result.insights], ensure_ascii=False)
    review.anomalies_json = json.dumps(result.anomalies, ensure_ascii=False)
    review.recommendations_json = json.dumps(result.recommendations, ensure_ascii=False)
    review.model_used = ai_outcome.usage.model
    if existing is None:
        db.add(review)
    db.commit()

    outcome.saved = True
    return outcome
