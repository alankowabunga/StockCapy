import pandas as pd
import pytest

from portfolio.indicators import (
    bollinger_bands,
    compute_indicators,
    moving_average_series,
    relative_strength_index,
    simple_moving_average,
)

# Wilder's own worked example (via StockCharts). The first RSI, formed from exactly
# 14 price changes, is the SMA-based seed value.
WILDER_CLOSES = pd.Series(
    [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
     45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
)


def test_rsi_matches_wilders_seed_value():
    assert relative_strength_index(WILDER_CLOSES) == pytest.approx(70.46, abs=0.01)


def test_rsi_smooths_the_next_bar_with_wilder_weighting():
    extended = pd.concat([WILDER_CLOSES, pd.Series([45.28])], ignore_index=True)
    assert relative_strength_index(extended) == pytest.approx(57.42, abs=0.01)


def test_rsi_is_100_when_every_bar_rises():
    assert relative_strength_index(pd.Series(range(1, 40))) == 100.0


def test_rsi_is_0_when_every_bar_falls():
    assert relative_strength_index(pd.Series(range(40, 1, -1))) == 0.0


def test_rsi_needs_more_than_period_closes():
    assert relative_strength_index(pd.Series([1.0] * 14), period=14) is None


def test_flat_series_has_no_gains_or_losses():
    # No movement at all: neither overbought nor oversold, so a neutral 50.
    assert relative_strength_index(pd.Series([50.0] * 30)) == 50.0


def test_sma_averages_the_trailing_window():
    assert simple_moving_average(pd.Series(range(1, 11)), 5) == 8.0


def test_sma_returns_none_without_a_full_window():
    assert simple_moving_average(pd.Series([1.0, 2.0]), 5) is None


def test_bollinger_bands_are_symmetric_around_the_mean():
    bands = bollinger_bands(pd.Series(range(10, 50, 2)))
    assert bands.middle == pytest.approx(29.0)
    assert bands.upper == pytest.approx(52.065, abs=0.01)
    assert bands.lower == pytest.approx(5.935, abs=0.01)
    assert bands.middle - bands.lower == pytest.approx(bands.upper - bands.middle)


def test_snapshot_reports_bias_to_each_trend_line():
    closes = pd.Series([100.0] * 199 + [90.0])
    snapshot = compute_indicators("X", closes, short_window=60, long_window=200)
    assert snapshot.last_close == 90.0
    # 199 hundreds + one ninety, over 200 -> mean 99.95; 90 sits ~9.95% below it.
    assert snapshot.bias_to_sma_long_percent == pytest.approx((90 / 99.95 - 1) * 100, abs=0.01)


def test_snapshot_is_none_without_any_prices():
    assert compute_indicators("X", pd.Series(dtype="float64")) is None


def test_gaps_are_dropped_before_averaging():
    assert simple_moving_average(pd.Series([2.0, None, 4.0, None, 6.0]), 3) == 4.0


def test_moving_average_series_leaves_the_unfilled_window_empty():
    averages = moving_average_series(pd.Series([1.0, 2.0, 3.0, 4.0]), 3)
    assert averages.iloc[:2].isna().all()
    assert averages.iloc[2] == pytest.approx(2.0)
    assert averages.iloc[3] == pytest.approx(3.0)


def test_moving_average_series_is_empty_when_the_window_never_fills():
    assert moving_average_series(pd.Series([1.0, 2.0]), 5).isna().all()


def test_moving_average_series_drops_gaps_before_averaging():
    averages = moving_average_series(pd.Series([2.0, None, 4.0, 6.0]), 3)
    assert averages.dropna().iloc[-1] == pytest.approx(4.0)
