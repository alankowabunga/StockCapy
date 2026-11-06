from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .market import MarketDataClient

MIN_OBSERVATIONS = 120
MAX_STABLE_SPREAD_RATIO = 0.8
LOOKBACK_YEARS = 5


@dataclass(frozen=True)
class HistoricalPriceEarnings:
    """Distribution of the stock's own trailing P/E, from reported annual EPS."""

    symbol: str
    median_pe: float | None
    lower_quartile_pe: float | None
    upper_quartile_pe: float | None
    observation_days: int

    @property
    def is_usable(self) -> bool:
        return self.median_pe is not None and self.median_pe > 0

    @property
    def quartile_spread_ratio(self) -> float | None:
        """Wide spread means EPS moved so fast that trailing P/E barely means anything."""
        if not self.is_usable or self.lower_quartile_pe is None:
            return None
        return (self.upper_quartile_pe - self.lower_quartile_pe) / self.median_pe

    @property
    def is_stable(self) -> bool:
        spread = self.quartile_spread_ratio
        return spread is not None and spread <= MAX_STABLE_SPREAD_RATIO


def _reported_eps_series(annual_eps: dict, price_index: pd.DatetimeIndex) -> pd.Series:
    """Steps EPS forward from each fiscal year end, so no day uses unpublished figures."""
    if not annual_eps:
        return pd.Series(dtype="float64")
    ordered = pd.Series(annual_eps).sort_index()
    return ordered.reindex(ordered.index.union(price_index)).ffill().reindex(price_index)


def _summarize(symbol: str, price_earnings: pd.Series) -> HistoricalPriceEarnings:
    usable = price_earnings.dropna()
    usable = usable[(usable > 0) & (usable < 500)]
    if len(usable) < MIN_OBSERVATIONS:
        return HistoricalPriceEarnings(symbol, None, None, None, len(usable))
    return HistoricalPriceEarnings(
        symbol=symbol,
        median_pe=float(usable.median()),
        lower_quartile_pe=float(usable.quantile(0.25)),
        upper_quartile_pe=float(usable.quantile(0.75)),
        observation_days=len(usable),
    )


def compute_historical_price_earnings(
    client: MarketDataClient, symbol: str, lookback_years: int = LOOKBACK_YEARS
) -> HistoricalPriceEarnings:
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=365 * lookback_years)
    closes = client.close_prices(symbol, start, end)
    annual_eps = client.annual_diluted_eps(symbol)
    if closes.empty or not annual_eps:
        return HistoricalPriceEarnings(symbol, None, None, None, 0)
    eps_series = _reported_eps_series(annual_eps, pd.DatetimeIndex(closes.index))
    return _summarize(symbol, closes / eps_series)
