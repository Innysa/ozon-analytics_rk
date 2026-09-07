"""Tests for the AI-generated advertising-campaign review service:
aggregation of per-campaign daily statistics into the AIProvider's input
shape, and the generate/save/update-in-place flow (see
app.services.advertising_ai_review_service). Uses a fake, duck-typed
AIProvider — DemoProvider's own deterministic behavior is covered separately
by test_ai_provider.py."""
import json
from datetime import date

from app.models.advertising_ai_review import AdvertisingAiReview
from app.models.advertising_campaign import AdvertisingCampaign
from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
from app.services.advertising_ai_review_service import _aggregate_campaigns, generate_advertising_ai_review
from app.services.ai.schemas import AdvertisingAnalysisResult, AdvertisingCampaignInsight, AIUsage, AnalyzeAdvertisingOutcome


class FakeAIProvider:
    def __init__(self, outcome: AnalyzeAdvertisingOutcome | None = None):
        self.calls: list[dict] = []
        self._outcome = outcome or AnalyzeAdvertisingOutcome(
            result=AdvertisingAnalysisResult(overview="Тестовый обзор"), usage=AIUsage(model="fake"), success=True
        )

    def analyze_advertising_campaigns(self, *, store_name, period_start, period_end, campaigns):
        self.calls.append(
            {"store_name": store_name, "period_start": period_start, "period_end": period_end, "campaigns": campaigns}
        )
        return self._outcome


def _make_campaign(db_session, store_id: str, ozon_campaign_id: str = "111", name: str = "Кампания А"):
    campaign = AdvertisingCampaign(
        store_id=store_id, ozon_campaign_id=ozon_campaign_id, name=name,
        campaign_type="SKU", state="CAMPAIGN_STATE_RUNNING",
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


def _add_daily(db_session, *, store_id: str, ozon_campaign_id: str, day: date, spend: float, impressions: int, clicks: int):
    row = AdvertisingDailyStatistic(
        store_id=store_id, ozon_campaign_id=ozon_campaign_id, ozon_sku="999", date=day,
        spend_rub=spend, impressions=impressions, clicks=clicks, source="ozon_performance_api",
    )
    db_session.add(row)


def test_aggregate_campaigns_sums_across_skus_and_days(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_campaign(db_session, d["store_a"].id, "111", "Кампания А")
    _add_daily(db_session, store_id=d["store_a"].id, ozon_campaign_id="111", day=date(2026, 8, 1), spend=100, impressions=1000, clicks=50)
    _add_daily(db_session, store_id=d["store_a"].id, ozon_campaign_id="111", day=date(2026, 8, 2), spend=150, impressions=1200, clicks=40)
    db_session.commit()

    campaigns = _aggregate_campaigns(db_session, d["store_a"].id, date(2026, 8, 1), date(2026, 8, 2))

    assert len(campaigns) == 1
    c = campaigns[0]
    assert c["ozon_campaign_id"] == "111"
    assert c["name"] == "Кампания А"
    assert c["total_spend_rub"] == 250
    assert c["total_impressions"] == 2200
    assert c["total_clicks"] == 90
    assert c["ctr_pct"] == round(90 / 2200 * 100, 2)
    assert [row["date"] for row in c["daily"]] == ["2026-08-01", "2026-08-02"]


def test_aggregate_campaigns_skips_campaigns_without_any_daily_data(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_campaign(db_session, d["store_a"].id, "111", "Без данных")
    db_session.commit()

    assert _aggregate_campaigns(db_session, d["store_a"].id, date(2026, 8, 1), date(2026, 8, 2)) == []


def test_aggregate_campaigns_sorted_by_spend_descending(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_campaign(db_session, d["store_a"].id, "111", "Дешёвая")
    _make_campaign(db_session, d["store_a"].id, "222", "Дорогая")
    _add_daily(db_session, store_id=d["store_a"].id, ozon_campaign_id="111", day=date(2026, 8, 1), spend=50, impressions=100, clicks=5)
    _add_daily(db_session, store_id=d["store_a"].id, ozon_campaign_id="222", day=date(2026, 8, 1), spend=500, impressions=1000, clicks=50)
    db_session.commit()

    campaigns = _aggregate_campaigns(db_session, d["store_a"].id, date(2026, 8, 1), date(2026, 8, 1))
    assert [c["ozon_campaign_id"] for c in campaigns] == ["222", "111"]


def test_generate_review_saves_a_new_row(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_campaign(db_session, d["store_a"].id, "111", "Кампания А")
    _add_daily(db_session, store_id=d["store_a"].id, ozon_campaign_id="111", day=date(2026, 8, 1), spend=100, impressions=1000, clicks=50)
    db_session.commit()

    ai = FakeAIProvider(
        AnalyzeAdvertisingOutcome(
            result=AdvertisingAnalysisResult(
                overview="Расход стабилен.",
                insights=[AdvertisingCampaignInsight(ozon_campaign_id="111", campaign_name="Кампания А", assessment="neutral", note="ок")],
                anomalies=[],
                recommendations=["Ничего не менять"],
            ),
            usage=AIUsage(model="fake-model"),
            success=True,
        )
    )

    outcome = generate_advertising_ai_review(
        db_session, store_id=d["store_a"].id, ai_provider=ai, date_from=date(2026, 8, 1), date_to=date(2026, 8, 1)
    )

    assert outcome.saved is True
    assert outcome.campaigns_analyzed == 1
    assert outcome.errors == []
    assert len(ai.calls) == 1
    assert ai.calls[0]["campaigns"][0]["ozon_campaign_id"] == "111"
    assert ai.calls[0]["store_name"] == d["store_a"].name

    row = db_session.query(AdvertisingAiReview).filter(AdvertisingAiReview.store_id == d["store_a"].id).one()
    assert row.overview == "Расход стабилен."
    assert row.model_used == "fake-model"
    assert row.campaigns_analyzed == 1
    assert json.loads(row.recommendations_json) == ["Ничего не менять"]
    assert json.loads(row.insights_json)[0]["assessment"] == "neutral"


def test_generate_review_with_no_data_reports_a_clear_error_and_never_calls_ai(db_session, two_stores_with_users):
    d = two_stores_with_users
    ai = FakeAIProvider()

    outcome = generate_advertising_ai_review(
        db_session, store_id=d["store_a"].id, ai_provider=ai, date_from=date(2026, 8, 1), date_to=date(2026, 8, 1)
    )

    assert outcome.saved is False
    assert outcome.campaigns_analyzed == 0
    assert any("автосбор статистики рекламы" in e for e in outcome.errors)
    assert ai.calls == []
    assert db_session.query(AdvertisingAiReview).count() == 0


def test_generate_review_failure_from_ai_provider_is_not_saved(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_campaign(db_session, d["store_a"].id, "111", "Кампания А")
    _add_daily(db_session, store_id=d["store_a"].id, ozon_campaign_id="111", day=date(2026, 8, 1), spend=100, impressions=1000, clicks=50)
    db_session.commit()

    ai = FakeAIProvider(AnalyzeAdvertisingOutcome(result=None, usage=AIUsage(model="fake"), success=False, error_message="таймаут"))
    outcome = generate_advertising_ai_review(
        db_session, store_id=d["store_a"].id, ai_provider=ai, date_from=date(2026, 8, 1), date_to=date(2026, 8, 1)
    )

    assert outcome.saved is False
    assert any("таймаут" in e for e in outcome.errors)
    assert db_session.query(AdvertisingAiReview).count() == 0


def test_generate_review_for_same_period_updates_in_place_not_duplicated(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_campaign(db_session, d["store_a"].id, "111", "Кампания А")
    _add_daily(db_session, store_id=d["store_a"].id, ozon_campaign_id="111", day=date(2026, 8, 1), spend=100, impressions=1000, clicks=50)
    db_session.commit()

    ai1 = FakeAIProvider(AnalyzeAdvertisingOutcome(result=AdvertisingAnalysisResult(overview="Версия 1"), usage=AIUsage(model="fake"), success=True))
    generate_advertising_ai_review(db_session, store_id=d["store_a"].id, ai_provider=ai1, date_from=date(2026, 8, 1), date_to=date(2026, 8, 1))

    ai2 = FakeAIProvider(AnalyzeAdvertisingOutcome(result=AdvertisingAnalysisResult(overview="Версия 2"), usage=AIUsage(model="fake"), success=True))
    generate_advertising_ai_review(db_session, store_id=d["store_a"].id, ai_provider=ai2, date_from=date(2026, 8, 1), date_to=date(2026, 8, 1))

    rows = db_session.query(AdvertisingAiReview).filter(AdvertisingAiReview.store_id == d["store_a"].id).all()
    assert len(rows) == 1
    assert rows[0].overview == "Версия 2"


def test_generate_review_different_periods_build_history(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_campaign(db_session, d["store_a"].id, "111", "Кампания А")
    _add_daily(db_session, store_id=d["store_a"].id, ozon_campaign_id="111", day=date(2026, 8, 1), spend=100, impressions=1000, clicks=50)
    _add_daily(db_session, store_id=d["store_a"].id, ozon_campaign_id="111", day=date(2026, 8, 2), spend=100, impressions=1000, clicks=50)
    db_session.commit()

    ai1 = FakeAIProvider(AnalyzeAdvertisingOutcome(result=AdvertisingAnalysisResult(overview="День 1"), usage=AIUsage(model="fake"), success=True))
    generate_advertising_ai_review(db_session, store_id=d["store_a"].id, ai_provider=ai1, date_from=date(2026, 8, 1), date_to=date(2026, 8, 1))

    ai2 = FakeAIProvider(AnalyzeAdvertisingOutcome(result=AdvertisingAnalysisResult(overview="День 2"), usage=AIUsage(model="fake"), success=True))
    generate_advertising_ai_review(db_session, store_id=d["store_a"].id, ai_provider=ai2, date_from=date(2026, 8, 2), date_to=date(2026, 8, 2))

    rows = db_session.query(AdvertisingAiReview).filter(AdvertisingAiReview.store_id == d["store_a"].id).all()
    assert len(rows) == 2
    assert {r.overview for r in rows} == {"День 1", "День 2"}


def test_generate_review_defaults_to_yesterday_ending_lookback_window(db_session, two_stores_with_users, monkeypatch):
    import app.services.advertising_ai_review_service as svc

    class _FixedDatetime(svc.datetime):
        @classmethod
        def now(cls, tz=None):
            return svc.datetime(2026, 9, 7, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(svc, "datetime", _FixedDatetime)

    from app.core import config as config_module
    config_module.get_settings.cache_clear()
    monkeypatch.setenv("ADVERTISING_AI_REVIEW_LOOKBACK_DAYS", "3")
    config_module.get_settings.cache_clear()

    d = two_stores_with_users
    _make_campaign(db_session, d["store_a"].id, "111", "Кампания А")
    _add_daily(db_session, store_id=d["store_a"].id, ozon_campaign_id="111", day=date(2026, 9, 4), spend=10, impressions=10, clicks=1)
    _add_daily(db_session, store_id=d["store_a"].id, ozon_campaign_id="111", day=date(2026, 9, 6), spend=10, impressions=10, clicks=1)
    db_session.commit()

    ai = FakeAIProvider()
    outcome = generate_advertising_ai_review(db_session, store_id=d["store_a"].id, ai_provider=ai)

    config_module.get_settings.cache_clear()

    assert outcome.saved is True
    assert ai.calls[0]["period_start"] == date(2026, 9, 4)
    assert ai.calls[0]["period_end"] == date(2026, 9, 6)  # "yesterday", not "today" (2026-09-07)
