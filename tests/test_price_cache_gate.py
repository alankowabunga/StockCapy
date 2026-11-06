"""Production symptom: price_fetch_log said 'fetched today' while price_history stopped
four days earlier, because the day's first fetch ran before yfinance published the bar
and the marker then suppressed every later attempt for 24 hours."""
from datetime import date, timedelta

import pandas as pd
import pytest
from conftest import make_settings

from portfolio.repository import mark_price_history_fetched, save_price_history
from portfolio.service import _fetch_adjusted_closes
from portfolio.store import open_store

LOOKBACK = 400


class CountingClient:
    def __init__(self, last_day: date):
        self.last_day = last_day
        self.calls = 0

    def close_prices(self, symbol, start, end):
        self.calls += 1
        index = pd.date_range(end=pd.Timestamp(self.last_day), periods=30, freq="D")
        return pd.Series([100.0 + i for i in range(30)], index=index)


def _seed_cache(settings, symbol: str, last_day: date, marked_on: date) -> None:
    index = pd.date_range(end=pd.Timestamp(last_day), periods=30, freq="D")
    with open_store(settings.database_path) as connection:
        save_price_history(connection, symbol, pd.Series([50.0] * 30, index=index))
        mark_price_history_fetched(connection, symbol, marked_on)


def test_a_cache_behind_the_last_session_is_refetched_despite_todays_marker(tmp_path):
    """The exact production state: marked fetched today, content four days old."""
    settings = make_settings(tmp_path)
    _seed_cache(settings, "MSFT", date.today() - timedelta(days=4), date.today())
    client = CountingClient(date.today())
    closes = _fetch_adjusted_closes(settings, client, "MSFT", LOOKBACK)
    assert client.calls == 1
    assert closes.index[-1].date() == date.today()


def test_a_current_cache_is_served_without_any_network_call(tmp_path):
    settings = make_settings(tmp_path)
    _seed_cache(settings, "MSFT", date.today(), date.today())
    client = CountingClient(date.today())
    _fetch_adjusted_closes(settings, client, "MSFT", LOOKBACK)
    assert client.calls == 0


def test_repeated_ticks_with_a_current_cache_never_refetch(tmp_path):
    """The gate still has to keep a 30-minute loop off the network."""
    settings = make_settings(tmp_path)
    client = CountingClient(date.today())
    _fetch_adjusted_closes(settings, client, "MSFT", LOOKBACK)
    for _ in range(5):
        _fetch_adjusted_closes(settings, client, "MSFT", LOOKBACK)
    assert client.calls == 1


def test_a_failed_download_leaves_the_stale_cache_standing(tmp_path):
    settings = make_settings(tmp_path)
    stale_day = date.today() - timedelta(days=20)
    _seed_cache(settings, "MSFT", stale_day, date.today())

    class FailingClient(CountingClient):
        def close_prices(self, symbol, start, end):
            self.calls += 1
            raise OSError("rate limited")

    client = FailingClient(date.today())
    closes = _fetch_adjusted_closes(settings, client, "MSFT", LOOKBACK)
    assert client.calls == 1
    assert closes.index[-1].date() == stale_day  # old data, but the freshness gate catches it


def test_an_empty_cache_fetches(tmp_path):
    settings = make_settings(tmp_path)
    client = CountingClient(date.today())
    assert not _fetch_adjusted_closes(settings, client, "NVDA", LOOKBACK).empty
    assert client.calls == 1
