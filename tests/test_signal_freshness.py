"""A rule describes where the price sits now. Built from weeks-old closes it describes
some earlier week instead — and that is exactly what reached the user's phone for three
weeks in production, with the stale price printed as if current."""
from datetime import date, timedelta

import pandas as pd
import pytest
from conftest import make_settings

from portfolio.market import Quote
from portfolio.service import SignalScope, evaluate_signals, scan_signals
from portfolio.signals import (
    MAX_SIGNAL_DATA_AGE_DAYS,
    is_signal_data_stale,
    signal_data_age_days,
)

TODAY = date(2026, 10, 13)


def _closes_ending(last_day: date, periods: int = 60) -> pd.Series:
    index = pd.date_range(end=pd.Timestamp(last_day), periods=periods, freq="D")
    return pd.Series([100.0] * (periods - 1) + [80.0], index=index)


class FeedStoppedClient:
    """The real failure: fetches keep failing, so the cache stops advancing while the
    live quote still answers — every indicator is then computed on old closes."""

    def __init__(self, last_day: date):
        self.last_day = last_day

    def close_prices(self, symbol, start, end):
        if symbol == "SPY":
            index = pd.date_range(end=pd.Timestamp(self.last_day), periods=60, freq="D")
            return pd.Series([500.0] * 60, index=index)
        return _closes_ending(self.last_day)

    def quote(self, symbol):
        return Quote(symbol=symbol, last_price=541.15, previous_close=535.07, currency="USD")


@pytest.mark.parametrize("age,expected", [(0, False), (MAX_SIGNAL_DATA_AGE_DAYS, False),
                                          (MAX_SIGNAL_DATA_AGE_DAYS + 1, True), (25, True)])
def test_staleness_boundary(age, expected):
    as_of = (TODAY - timedelta(days=age)).isoformat()
    assert is_signal_data_stale(as_of, TODAY) is expected


def test_a_missing_date_counts_as_stale():
    """Freshness that cannot be verified is not freshness."""
    assert is_signal_data_stale(None, TODAY) is True
    assert signal_data_age_days(None, TODAY) is None


def test_fresh_data_still_produces_a_signal(tmp_path):
    settings = make_settings(tmp_path)
    client = FeedStoppedClient(date.today())
    assert len(evaluate_signals(settings, client, scope=SignalScope(symbols=["NVDA"]))) == 1


def test_stale_closes_produce_no_signal_even_though_the_quote_works(tmp_path):
    settings = make_settings(tmp_path)
    client = FeedStoppedClient(date.today() - timedelta(days=25))
    assert evaluate_signals(settings, client, scope=SignalScope(symbols=["NVDA"])) == []


def test_staleness_is_reported_rather_than_passing_as_quiet(tmp_path):
    """One pass yields both halves: an empty signal list plus a named stale symbol,
    so a broken feed cannot be mistaken for a calm market."""
    settings = make_settings(tmp_path)
    client = FeedStoppedClient(date.today() - timedelta(days=25))
    scan = scan_signals(settings, client, SignalScope(symbols=["NVDA"]))
    assert scan.signals == []
    stale = scan.stale
    assert len(stale) == 1
    assert stale[0].symbol == "NVDA"
    assert stale[0].age_days == 25
    assert "25 天前" in stale[0].describe_age


def test_a_healthy_feed_reports_nothing_stale(tmp_path):
    settings = make_settings(tmp_path)
    client = FeedStoppedClient(date.today())
    assert scan_signals(settings, client, SignalScope(symbols=["NVDA"])).stale == []


def test_the_endpoint_shape_lets_a_broken_feed_be_told_from_a_calm_market(tmp_path):
    """Both halves must travel together: `signals: []` alone is ambiguous, and the UI
    printed 「沒有任何標的觸發」 for symbols it had never judged."""
    # Separate data dirs: the close cache is per-symbol-per-day, so one settings object
    # would hand the second scan the first client's prices.
    broken = scan_signals(
        make_settings(tmp_path / "broken"),
        FeedStoppedClient(date.today() - timedelta(days=25)),
        SignalScope(symbols=["NVDA"]),
    )
    calm = scan_signals(
        make_settings(tmp_path / "calm"), _NoTriggerClient(), SignalScope(symbols=["NVDA"])
    )
    assert broken.signals == [] and broken.stale != []
    assert calm.signals == [] and calm.stale == []


class _NoTriggerClient(FeedStoppedClient):
    """Fresh closes that sit flat, so no rule fires and nothing is stale."""

    def __init__(self):
        super().__init__(date.today())

    def close_prices(self, symbol, start, end):
        index = pd.date_range(end=pd.Timestamp(self.last_day), periods=60, freq="D")
        return pd.Series([100.0] * 60, index=index)
