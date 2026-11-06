import pandas as pd
import pytest

from portfolio.indicators import BollingerBands, IndicatorSnapshot
from portfolio.signals import (
    BUY,
    SELL,
    MarketContext,
    PositionPricing,
    PriceHistory,
    SignalCondition,
    SignalThresholds,
    evaluate_signal,
    evaluate_signal_from_closes,
)


def snapshot(
    *,
    last_close: float,
    rsi: float | None = 50.0,
    sma_short: float | None = 100.0,
    sma_long: float | None = 100.0,
    lower: float = 0.0,
    upper: float = 1e9,
) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        symbol="TEST",
        last_close=last_close,
        rsi=rsi,
        sma_short=sma_short,
        sma_long=sma_long,
        bands=BollingerBands(lower=lower, middle=(lower + upper) / 2, upper=upper),
    )


def _codes(signal):
    return {condition.name for condition in signal.conditions}


def test_no_signal_when_price_sits_in_the_middle_of_every_rule():
    result = evaluate_signal(
        snapshot(last_close=100.0), pricing=PositionPricing(average_cost=100.0)
    )
    assert result is None


def test_buy_stacks_every_cheap_condition():
    signal = evaluate_signal(
        snapshot(last_close=85.0, rsi=25.0, sma_short=100.0, lower=90.0, upper=120.0),
        pricing=PositionPricing(average_cost=100.0),
    )
    assert signal.direction == BUY
    assert signal.severity == 4
    assert _codes(signal) == {
        "BELOW_AVERAGE_COST",
        "BELOW_SHORT_TREND",
        "OVERSOLD_RSI",
        "BELOW_LOWER_BAND",
    }


def test_sell_conditions_fire_on_the_expensive_side():
    signal = evaluate_signal(
        snapshot(last_close=120.0, rsi=75.0, sma_short=100.0, lower=80.0, upper=110.0)
    )
    assert signal.direction == SELL
    assert _codes(signal) == {"ABOVE_SHORT_TREND", "OVERBOUGHT_RSI", "ABOVE_UPPER_BAND"}


def test_buy_wins_when_both_sides_somehow_fire():
    # Below its own average cost yet above the trend line: contradictory, buy wins.
    signal = evaluate_signal(
        snapshot(last_close=120.0, rsi=50.0, sma_short=100.0, lower=80.0, upper=200.0),
        pricing=PositionPricing(average_cost=200.0),
    )
    assert signal.direction == BUY
    assert _codes(signal) == {"BELOW_AVERAGE_COST"}


def test_average_cost_discount_uses_the_live_price_not_the_adjusted_close():
    # Technicals see 100 (nothing fires); the nominal live price 85 drives the cost rule.
    signal = evaluate_signal(
        snapshot(last_close=100.0, sma_short=100.0),
        pricing=PositionPricing(last_price=85.0, average_cost=100.0),
    )
    assert _codes(signal) == {"BELOW_AVERAGE_COST"}
    assert signal.discount_to_average_cost_percent == pytest.approx(-15.0)


def test_trend_bias_triggers_past_the_threshold_but_not_short_of_it():
    assert evaluate_signal(snapshot(last_close=89.0, sma_short=100.0)) is not None  # -11%
    assert evaluate_signal(snapshot(last_close=91.0, sma_short=100.0)) is None  # -9%


def test_rsi_boundary_is_strict():
    assert evaluate_signal(snapshot(last_close=100.0, rsi=30.0, sma_short=100.0)) is None
    assert evaluate_signal(snapshot(last_close=100.0, rsi=29.9, sma_short=100.0)) is not None


def test_missing_indicator_is_skipped_not_treated_as_a_trigger():
    signal = evaluate_signal(
        snapshot(last_close=85.0, rsi=None, sma_short=None, lower=90.0, upper=120.0)
    )
    # Only the band condition can be judged; the None-valued rules are silent.
    assert _codes(signal) == {"BELOW_LOWER_BAND"}


def test_a_buy_during_a_broad_selloff_is_flagged_as_probably_market_wide():
    context = MarketContext(benchmark_symbol="SPY", change_percent=-6.0)
    signal = evaluate_signal(
        snapshot(last_close=85.0, sma_short=100.0), market_context=context
    )
    assert signal.direction == BUY
    assert signal.likely_market_wide


def test_a_buy_during_a_calm_market_is_stock_specific():
    context = MarketContext(benchmark_symbol="SPY", change_percent=-1.0)
    signal = evaluate_signal(
        snapshot(last_close=85.0, sma_short=100.0), market_context=context
    )
    assert not signal.likely_market_wide


def test_a_sell_is_market_wide_only_during_a_broad_rally():
    rally = MarketContext(benchmark_symbol="SPY", change_percent=7.0)
    signal = evaluate_signal(snapshot(last_close=130.0, sma_short=100.0), market_context=rally)
    assert signal.direction == SELL
    assert signal.likely_market_wide


def test_custom_thresholds_are_honoured():
    loose = SignalThresholds(short_trend_bias_percent=5.0)
    assert evaluate_signal(snapshot(last_close=94.0, sma_short=100.0)) is None  # -6% vs default 10%
    assert evaluate_signal(snapshot(last_close=94.0, sma_short=100.0), thresholds=loose) is not None


def test_condition_labels_carry_a_direction():
    assert SignalCondition.BELOW_AVERAGE_COST.direction == BUY
    assert SignalCondition.OVERBOUGHT_RSI.direction == SELL


def test_from_closes_returns_none_when_history_is_empty():
    assert evaluate_signal_from_closes(PriceHistory("X", pd.Series(dtype="float64"))) is None


def test_from_closes_builds_and_evaluates_in_one_step():
    closes = pd.Series([100.0] * 59 + [80.0])  # last 20% below the 60-day mean
    signal = evaluate_signal_from_closes(PriceHistory("X", closes))
    assert signal is not None
    assert signal.direction == BUY
