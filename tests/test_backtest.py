from datetime import date

import pandas as pd
import pytest

from portfolio.backtest import MIN_EVALUABLE_HISTORY, backtest_signals
from portfolio.signals import PriceHistory

DROP = pd.Series(
    [100.0] * 80 + [60.0] * 40,
    index=pd.date_range("2024-01-01", periods=120, freq="D"),
)
FLAT = pd.Series([100.0] * 100, index=pd.date_range("2024-01-01", periods=100, freq="D"))


def test_evaluable_days_start_once_the_shortest_indicator_is_available():
    result = backtest_signals(PriceHistory("X", DROP))
    assert result.evaluable_days == len(DROP) - (MIN_EVALUABLE_HISTORY - 1)


def test_a_sustained_drop_is_one_merged_buy_episode():
    result = backtest_signals(PriceHistory("X", DROP))
    assert result.episode_count == 1
    episode = result.episodes[0]
    assert episode.direction == "BUY"
    assert episode.start == date(2024, 3, 21)  # first day at the lower price
    assert episode.end == date(2024, 4, 29)  # last day in the series
    assert episode.peak_severity == 3


def test_trigger_day_ratio_reflects_how_often_a_rule_was_on():
    result = backtest_signals(PriceHistory("X", DROP))
    assert result.trigger_days == 40
    assert result.trigger_day_ratio == result.trigger_days / result.evaluable_days


def test_a_flat_series_never_triggers():
    result = backtest_signals(PriceHistory("X", FLAT))
    assert result.trigger_days == 0
    assert result.episode_count == 0
    assert result.trigger_day_ratio == 0.0


def test_too_little_history_yields_an_empty_result():
    result = backtest_signals(PriceHistory("X", pd.Series(dtype="float64")))
    assert result.evaluable_days == 0
    assert result.episodes == ()
    assert result.trigger_day_ratio == 0.0


RECOVER = pd.Series(
    [100.0] * 80 + [60.0] * 20 + [75.0] * 20 + [90.0] * 20,
    index=pd.date_range("2024-01-01", periods=140, freq="D"),
)


def test_forward_return_is_measured_once_per_entry_not_per_trigger_day():
    result = backtest_signals(PriceHistory("X", RECOVER), horizons=(20,))
    stats = result.forward_returns[0]
    # 20 trigger days in the buy episode, but only one entry -> one outcome.
    assert stats.sample_size == 1
    assert stats.median_return_percent == pytest.approx(25.0)  # 60 -> 75
    assert stats.win_rate_percent == 100.0


def test_forward_return_is_empty_when_the_horizon_runs_past_the_data():
    result = backtest_signals(PriceHistory("X", RECOVER), horizons=(60,))
    stats = result.forward_returns[0]
    assert stats.sample_size == 0
    assert stats.win_rate_percent is None
    assert stats.median_return_percent is None


def test_a_losing_signal_shows_a_sub_100_win_rate():
    # Buys the drop, then it keeps falling for the whole horizon.
    keeps_falling = pd.Series(
        [100.0] * 80 + [70.0] * 5 + [60.0] * 15,
        index=pd.date_range("2024-01-01", periods=100, freq="D"),
    )
    stats = backtest_signals(PriceHistory("X", keeps_falling), horizons=(15,)).forward_returns[0]
    assert stats.sample_size == 1
    assert stats.win_rate_percent == 0.0
    assert stats.median_return_percent < 0


def test_backtest_does_not_peek_at_future_prices():
    # Truncating the series must not change the verdict on the days that remain.
    full = backtest_signals(PriceHistory("X", DROP))
    truncated = backtest_signals(PriceHistory("X", DROP.iloc[:100]))
    shared = [e for e in full.episodes if e.start <= date(2024, 4, 9)]
    assert truncated.episodes[0].start == shared[0].start
