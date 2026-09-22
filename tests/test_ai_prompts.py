"""Tests for the pure prompt-building functions in app.services.ai.prompts —
no network, no AIProvider needed. Focused on the 2026-09-22 fix: a real
store with 60+ campaigns previously got AI insights for only ~3 of them
(see test_advertising_ai_review_service.py's own backfill test for the
service-level half of this fix)."""
from datetime import date

from app.services.ai.prompts import (
    ADVERTISING_JSON_CONTRACT,
    CAMPAIGNS_WITH_DAILY_DETAIL,
    build_advertising_analysis_prompt,
)


def _campaign(ozon_campaign_id: str, *, spend: float, daily: list[dict] | None = None) -> dict:
    return {
        "ozon_campaign_id": ozon_campaign_id,
        "name": f"Кампания {ozon_campaign_id}",
        "campaign_type": "SKU",
        "state": "CAMPAIGN_STATE_RUNNING",
        "daily_budget_rub": 100.0,
        "total_spend_rub": spend,
        "total_impressions": 1000,
        "total_clicks": 50,
        "ctr_pct": 5.0,
        "daily": daily or [{"date": "2026-08-01", "spend_rub": spend, "impressions": 1000, "clicks": 50}],
    }


def test_json_contract_requires_covering_every_campaign():
    """Regression: the model previously had no explicit instruction to cover
    ALL campaigns and silently picked a handful of examples instead."""
    assert "КАЖДУЮ кампанию" in ADVERTISING_JSON_CONTRACT


def test_prompt_lists_every_campaign_in_the_totals_block_even_beyond_the_daily_detail_cap():
    campaigns = [_campaign(str(n), spend=float(1000 - n)) for n in range(60)]  # sorted desc by spend, like _aggregate_campaigns

    prompt = build_advertising_analysis_prompt(
        store_name="Тест", period_start=date(2026, 8, 1), period_end=date(2026, 8, 7), campaigns=campaigns,
    )

    for c in campaigns:
        assert f"id={c['ozon_campaign_id']}" in prompt


def test_prompt_only_gives_daily_breakdown_to_the_top_n_campaigns_by_spend():
    campaigns = [_campaign(str(n), spend=float(1000 - n)) for n in range(60)]

    prompt = build_advertising_analysis_prompt(
        store_name="Тест", period_start=date(2026, 8, 1), period_end=date(2026, 8, 7), campaigns=campaigns,
    )

    daily_detail_section = prompt.split("Раскладка по дням")[1]
    for c in campaigns[:CAMPAIGNS_WITH_DAILY_DETAIL]:
        assert f"ozon_campaign_id={c['ozon_campaign_id']}" in daily_detail_section
    for c in campaigns[CAMPAIGNS_WITH_DAILY_DETAIL:]:
        assert f"ozon_campaign_id={c['ozon_campaign_id']}" not in daily_detail_section


def test_prompt_states_the_real_total_campaign_count():
    campaigns = [_campaign(str(n), spend=float(n)) for n in range(60)]

    prompt = build_advertising_analysis_prompt(
        store_name="Тест", period_start=date(2026, 8, 1), period_end=date(2026, 8, 7), campaigns=campaigns,
    )

    assert "все 60" in prompt
