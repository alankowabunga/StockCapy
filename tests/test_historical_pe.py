import pandas as pd
import pytest

from portfolio.historical_pe import (
    HistoricalPriceEarnings,
    _reported_eps_series,
    compute_historical_price_earnings,
)


class StubMarketDataClient:
    def __init__(self, closes: pd.Series, annual_eps: dict) -> None:
        self._closes = closes
        self._annual_eps = annual_eps

    def close_prices(self, symbol, start, end) -> pd.Series:
        return self._closes

    def annual_diluted_eps(self, symbol) -> dict:
        return self._annual_eps


def _daily_closes(price: float, days: int, start: str = "2024-01-01") -> pd.Series:
    index = pd.date_range(start=start, periods=days, freq="D")
    return pd.Series([price] * days, index=index)


def test_reported_eps_steps_forward_and_never_looks_ahead():
    """A day before the first fiscal year end has no reported EPS yet."""
    index = pd.DatetimeIndex(["2024-06-01", "2025-01-01", "2026-01-01"])
    eps = {pd.Timestamp("2024-12-31"): 10.0, pd.Timestamp("2025-12-31"): 12.0}
    series = _reported_eps_series(eps, index)
    assert pd.isna(series.iloc[0])
    assert series.iloc[1] == pytest.approx(10.0)
    assert series.iloc[2] == pytest.approx(12.0)


def test_empty_eps_yields_an_empty_series():
    assert _reported_eps_series({}, pd.DatetimeIndex(["2025-01-01"])).empty


def test_median_pe_is_computed_from_price_over_reported_eps():
    closes = _daily_closes(200.0, 400, start="2024-02-01")
    client = StubMarketDataClient(closes, {pd.Timestamp("2024-01-31"): 10.0})
    result = compute_historical_price_earnings(client, "X")
    assert result.median_pe == pytest.approx(20.0)
    assert result.is_usable
    assert result.observation_days == 400


def test_too_few_observations_is_not_usable():
    closes = _daily_closes(200.0, 30, start="2024-02-01")
    client = StubMarketDataClient(closes, {pd.Timestamp("2024-01-31"): 10.0})
    result = compute_historical_price_earnings(client, "X")
    assert not result.is_usable
    assert result.median_pe is None


def test_fund_without_eps_is_not_usable():
    client = StubMarketDataClient(_daily_closes(100.0, 500), {})
    result = compute_historical_price_earnings(client, "VTI")
    assert not result.is_usable
    assert result.observation_days == 0


def test_negative_eps_days_are_excluded():
    closes = _daily_closes(100.0, 400, start="2024-02-01")
    client = StubMarketDataClient(closes, {pd.Timestamp("2024-01-31"): -5.0})
    assert not compute_historical_price_earnings(client, "X").is_usable


def test_narrow_quartile_spread_is_stable():
    stable = HistoricalPriceEarnings("KO", median_pe=25.0, lower_quartile_pe=24.0,
                                     upper_quartile_pe=27.0, observation_days=900)
    assert stable.quartile_spread_ratio == pytest.approx(0.12)
    assert stable.is_stable


def test_wide_quartile_spread_is_unstable():
    """NVDA's real spread was 46-134 on a median of 74 - trailing P/E says little."""
    unstable = HistoricalPriceEarnings("NVDA", median_pe=73.9, lower_quartile_pe=46.0,
                                       upper_quartile_pe=134.3, observation_days=909)
    assert unstable.quartile_spread_ratio > 0.8
    assert not unstable.is_stable


def test_unusable_result_has_no_spread_and_is_not_stable():
    empty = HistoricalPriceEarnings("X", None, None, None, 0)
    assert empty.quartile_spread_ratio is None
    assert not empty.is_stable
