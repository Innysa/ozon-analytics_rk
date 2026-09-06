"""Tests for the per-campaign detail endpoint added for the expandable
campaign row on the "Реклама" page: aggregated totals plus a day-over-day
comparison — the latter only when at least two genuinely daily
(period_start == period_end) reports exist for that campaign, never
fabricated from a weekly/monthly report."""
from datetime import date

import pytest

from tests.conftest import login


def _make_campaign(db_session, store_id: str, *, campaign_type: str = "SKU", state: str = "CAMPAIGN_STATE_RUNNING"):
    from app.models.advertising_campaign import AdvertisingCampaign

    campaign = AdvertisingCampaign(
        store_id=store_id,
        ozon_campaign_id="12345",
        name="Тестовая кампания",
        campaign_type=campaign_type,
        state=state,
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


def _add_stat_row(db_session, *, store_id: str, campaign_id: str, ozon_campaign_id: str, day: date, spend: float, sales: float, impressions: int, clicks: int):
    from app.models.advertising_statistic import AdvertisingStatistic

    row = AdvertisingStatistic(
        store_id=store_id,
        campaign_id=campaign_id,
        ozon_sku="111",
        ozon_campaign_id=ozon_campaign_id,
        period_start=day,
        period_end=day,
        spend_rub=spend,
        sales_promo_rub=sales,
        impressions=impressions,
        clicks=clicks,
        source="xlsx_import",
    )
    db_session.add(row)


def test_no_data_reports_has_data_false(db_session, two_stores_with_users):
    from app.services.advertising_analytics_service import compute_campaign_detail

    d = two_stores_with_users
    result = compute_campaign_detail(db_session, store_id=d["store_a"].id, campaign_id="does-not-exist")
    assert result.has_data is False
    assert result.daily_comparison is None


def test_single_daily_report_has_totals_but_no_comparison(db_session, two_stores_with_users):
    from app.services.advertising_analytics_service import compute_campaign_detail

    d = two_stores_with_users
    campaign = _make_campaign(db_session, d["store_a"].id)
    _add_stat_row(
        db_session, store_id=d["store_a"].id, campaign_id=campaign.id, ozon_campaign_id=campaign.ozon_campaign_id,
        day=date(2026, 9, 1), spend=1000, sales=5000, impressions=2000, clicks=50,
    )
    db_session.flush()

    result = compute_campaign_detail(db_session, store_id=d["store_a"].id, campaign_id=campaign.id)
    assert result.has_data is True
    assert result.total_spend_rub == pytest.approx(1000)
    assert result.daily_comparison is None
    assert result.daily_comparison_unavailable_reason is not None
    assert "два" in result.daily_comparison_unavailable_reason


def test_two_daily_reports_produce_correct_comparison(db_session, two_stores_with_users):
    from app.services.advertising_analytics_service import compute_campaign_detail

    d = two_stores_with_users
    campaign = _make_campaign(db_session, d["store_a"].id)
    _add_stat_row(
        db_session, store_id=d["store_a"].id, campaign_id=campaign.id, ozon_campaign_id=campaign.ozon_campaign_id,
        day=date(2026, 9, 1), spend=1000, sales=5000, impressions=2000, clicks=50,
    )
    _add_stat_row(
        db_session, store_id=d["store_a"].id, campaign_id=campaign.id, ozon_campaign_id=campaign.ozon_campaign_id,
        day=date(2026, 9, 2), spend=1500, sales=4000, impressions=1800, clicks=60,
    )
    db_session.flush()

    result = compute_campaign_detail(db_session, store_id=d["store_a"].id, campaign_id=campaign.id)
    assert result.has_data is True
    # totals are summed across both days
    assert result.total_spend_rub == pytest.approx(2500)
    assert result.total_impressions == 3800

    comp = result.daily_comparison
    assert comp is not None
    assert comp.date_today == date(2026, 9, 2)
    assert comp.date_yesterday == date(2026, 9, 1)

    assert comp.spend_rub.today == pytest.approx(1500)
    assert comp.spend_rub.yesterday == pytest.approx(1000)
    assert comp.spend_rub.direction == "up"
    assert comp.spend_rub.delta_pct == pytest.approx(50.0)

    assert comp.impressions.direction == "down"  # 1800 < 2000
    assert comp.clicks.direction == "up"  # 60 > 50
    assert comp.sales_promo_rub.direction == "down"  # 4000 < 5000


def test_multiple_skus_same_day_are_summed_not_overwritten(db_session, two_stores_with_users):
    """A campaign with two SKUs on the same day must aggregate both, not
    silently keep only the last row for that date."""
    from app.services.advertising_analytics_service import compute_campaign_detail

    d = two_stores_with_users
    campaign = _make_campaign(db_session, d["store_a"].id)
    _add_stat_row(
        db_session, store_id=d["store_a"].id, campaign_id=campaign.id, ozon_campaign_id=campaign.ozon_campaign_id,
        day=date(2026, 9, 2), spend=100, sales=500, impressions=200, clicks=5,
    )
    from app.models.advertising_statistic import AdvertisingStatistic
    other_sku_row = AdvertisingStatistic(
        store_id=d["store_a"].id, campaign_id=campaign.id, ozon_sku="222", ozon_campaign_id=campaign.ozon_campaign_id,
        period_start=date(2026, 9, 2), period_end=date(2026, 9, 2),
        spend_rub=50, sales_promo_rub=250, impressions=100, clicks=2, source="xlsx_import",
    )
    db_session.add(other_sku_row)
    _add_stat_row(
        db_session, store_id=d["store_a"].id, campaign_id=campaign.id, ozon_campaign_id=campaign.ozon_campaign_id,
        day=date(2026, 9, 1), spend=10, sales=10, impressions=10, clicks=1,
    )
    db_session.flush()

    result = compute_campaign_detail(db_session, store_id=d["store_a"].id, campaign_id=campaign.id)
    comp = result.daily_comparison
    assert comp is not None
    assert comp.spend_rub.today == pytest.approx(150)  # 100 + 50, summed across both SKUs
    assert comp.impressions.today == pytest.approx(300)


def test_period_level_rows_never_treated_as_a_day(db_session, two_stores_with_users):
    """A weekly report (period_start != period_end) must be counted in the
    totals but must never be used to fake a daily comparison."""
    from app.models.advertising_statistic import AdvertisingStatistic
    from app.services.advertising_analytics_service import compute_campaign_detail

    d = two_stores_with_users
    campaign = _make_campaign(db_session, d["store_a"].id)
    weekly_row = AdvertisingStatistic(
        store_id=d["store_a"].id, campaign_id=campaign.id, ozon_sku="111", ozon_campaign_id=campaign.ozon_campaign_id,
        period_start=date(2026, 8, 25), period_end=date(2026, 8, 31),
        spend_rub=7000, sales_promo_rub=30000, impressions=15000, clicks=400, source="xlsx_import",
    )
    db_session.add(weekly_row)
    db_session.flush()

    result = compute_campaign_detail(db_session, store_id=d["store_a"].id, campaign_id=campaign.id)
    assert result.has_data is True
    assert result.total_spend_rub == pytest.approx(7000)  # still counted in totals
    assert result.daily_comparison is None  # but not usable as a "day"


def test_store_isolation_campaign_id_from_other_store_shows_no_data(db_session, two_stores_with_users):
    from app.services.advertising_analytics_service import compute_campaign_detail

    d = two_stores_with_users
    campaign_b = _make_campaign(db_session, d["store_b"].id)
    _add_stat_row(
        db_session, store_id=d["store_b"].id, campaign_id=campaign_b.id, ozon_campaign_id=campaign_b.ozon_campaign_id,
        day=date(2026, 9, 1), spend=999, sales=999, impressions=999, clicks=99,
    )
    db_session.flush()

    # store A asking about store B's campaign_id must see nothing, not store B's numbers
    result = compute_campaign_detail(db_session, store_id=d["store_a"].id, campaign_id=campaign_b.id)
    assert result.has_data is False


def test_campaign_detail_via_api_end_to_end(client, db_session, two_stores_with_users):
    d = two_stores_with_users
    campaign = _make_campaign(db_session, d["store_a"].id)
    _add_stat_row(
        db_session, store_id=d["store_a"].id, campaign_id=campaign.id, ozon_campaign_id=campaign.ozon_campaign_id,
        day=date(2026, 9, 1), spend=100, sales=200, impressions=100, clicks=5,
    )
    _add_stat_row(
        db_session, store_id=d["store_a"].id, campaign_id=campaign.id, ozon_campaign_id=campaign.ozon_campaign_id,
        day=date(2026, 9, 2), spend=200, sales=100, impressions=50, clicks=10,
    )
    db_session.commit()

    login(client, "owner_a@example.com", "password123")
    resp = client.get(f"/api/stores/{d['store_a'].id}/advertising/campaigns/{campaign.id}/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] is True
    assert body["daily_comparison"]["spend_rub"]["direction"] == "up"

    forbidden = client.get(f"/api/stores/{d['store_b'].id}/advertising/campaigns/{campaign.id}/detail")
    assert forbidden.status_code == 403
