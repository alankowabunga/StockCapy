from datetime import date, datetime, timezone

from portfolio.market_calendar import is_close_series_current
from portfolio.markets import TW_MARKET, US_MARKET

# Tuesday 09:00 UTC: Taiwan (closes 05:30 UTC, +3h publish buffer = 08:30) has already
# published today's bar; the US (closes 21:00 UTC) has not. So Monday's cache is still
# current for the US but stale for Taiwan — the freshness check must honour the market.
TUESDAY_0900_UTC = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
MONDAY = date(2026, 11, 9)
TUESDAY = date(2026, 11, 10)


def test_taiwan_expects_todays_bar_once_past_its_own_close():
    assert is_close_series_current(MONDAY, TUESDAY_0900_UTC, TW_MARKET) is False
    assert is_close_series_current(TUESDAY, TUESDAY_0900_UTC, TW_MARKET) is True


def test_us_still_accepts_mondays_bar_at_the_same_moment():
    # Same instant, same cached day — but the US session has not closed yet today.
    assert is_close_series_current(MONDAY, TUESDAY_0900_UTC, US_MARKET) is True
