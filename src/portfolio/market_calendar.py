from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

# US equities close 16:00 ET, which is 20:00 UTC in summer and 21:00 in winter. Using
# the later one keeps the rule correct all year without a timezone table.
US_CLOSE_HOUR_UTC = 21

# yfinance does not publish a daily bar the instant the bell rings. Before this much
# time has passed, a missing bar means "not out yet", not "the feed is broken".
PUBLISH_BUFFER_HOURS = 3

SATURDAY = 5


def _publish_deadline(day: date) -> datetime:
    close = datetime.combine(day, time(hour=US_CLOSE_HOUR_UTC), tzinfo=timezone.utc)
    return close + timedelta(hours=PUBLISH_BUFFER_HOURS)


def is_weekend(day: date) -> bool:
    return day.weekday() >= SATURDAY


def latest_expected_close_day(now_utc: datetime) -> date:
    """The most recent weekday whose close should already be published.

    Holidays are unknown here, so on a market holiday this over-expects by one day and
    the caller keeps retrying until the next session. That is the safe direction: an
    extra fetch costs a request, a missing one silently freezes every indicator."""
    day = now_utc.date()
    while is_weekend(day) or now_utc < _publish_deadline(day):
        day -= timedelta(days=1)
    return day


def is_close_series_current(last_close_day: date | None, now_utc: datetime) -> bool:
    """Judged on what the cache *contains*, never on when it was last written — the
    two came apart in production and froze prices for three weeks."""
    if last_close_day is None:
        return False
    return last_close_day >= latest_expected_close_day(now_utc)
