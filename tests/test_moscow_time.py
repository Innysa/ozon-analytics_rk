"""Tests for app.core.moscow_time — the shared UTC+3 helpers used by
order_daily_sync_service's in_process_at bucketing and (ИЗМЕНЕНО
2026-09-24) the funnel-vs-postings "Заказано" override's today-exclusion."""
from datetime import datetime, timezone

from app.core.moscow_time import moscow_today, utc_to_moscow_date


def test_utc_to_moscow_date_shifts_late_night_utc_to_next_day():
    dt = datetime(2026, 9, 4, 21, 30, tzinfo=timezone.utc)
    assert utc_to_moscow_date(dt).isoformat() == "2026-09-05"


def test_utc_to_moscow_date_keeps_same_day_for_early_utc_times():
    dt = datetime(2026, 9, 4, 5, 0, tzinfo=timezone.utc)
    assert utc_to_moscow_date(dt).isoformat() == "2026-09-04"


def test_moscow_today_matches_utc_plus_three_hours():
    from datetime import timedelta

    expected = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
    assert moscow_today() == expected
