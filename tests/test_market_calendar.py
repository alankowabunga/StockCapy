"""The cache gate used to ask 'did we fetch today?'. It froze because the first fetch
of the UTC day lands ~4 hours after the US close, before yfinance publishes the bar —
and then the gate locked that incomplete answer in for 24 hours."""
from datetime import date, datetime, timezone

import pytest

from portfolio.market_calendar import (
    is_close_series_current,
    latest_expected_close_day,
)


def _utc(year, month, day, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_before_the_publish_deadline_todays_bar_is_not_expected():
    # Tuesday 09:17 UTC: Tuesday's US session has not even opened yet.
    assert latest_expected_close_day(_utc(2026, 10, 13, 9, 17)) == date(2026, 10, 12)


def test_just_after_the_deadline_the_days_bar_is_expected():
    # 21:00 UTC close + 3h buffer = midnight.
    assert latest_expected_close_day(_utc(2026, 10, 13, 23, 59)) == date(2026, 10, 12)
    assert latest_expected_close_day(_utc(2026, 10, 14, 0, 1)) == date(2026, 10, 13)


@pytest.mark.parametrize("day,expected", [(17, date(2026, 10, 16)), (18, date(2026, 10, 16))])
def test_weekends_fall_back_to_friday(day, expected):
    assert latest_expected_close_day(_utc(2026, 10, day, 12)) == expected


def test_monday_morning_still_expects_friday():
    assert latest_expected_close_day(_utc(2026, 10, 19, 9)) == date(2026, 10, 16)


def test_a_cache_holding_the_expected_day_is_current():
    assert is_close_series_current(date(2026, 10, 12), _utc(2026, 10, 13, 9, 17))


def test_the_exact_production_symptom_is_detected():
    """Cache stuck at Friday 10-09 while Monday 10-12 was published and available."""
    assert not is_close_series_current(date(2026, 10, 9), _utc(2026, 10, 13, 9, 17))


def test_an_empty_cache_is_never_current():
    assert not is_close_series_current(None, _utc(2026, 10, 13, 9, 17))


def test_a_future_dated_cache_counts_as_current_rather_than_triggering_refetch():
    assert is_close_series_current(date(2026, 10, 20), _utc(2026, 10, 13, 9, 17))
