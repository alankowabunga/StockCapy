from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Textbook defaults. They live here so the rule engine and the backtester read the
# same numbers, and so a change is made in one place.
RSI_PERIOD = 14
SHORT_TREND_WINDOW = 60
LONG_TREND_WINDOW = 200
BOLLINGER_WINDOW = 20
TRADING_DAYS_PER_YEAR = 252  # the window for the 52-week high/low
BOLLINGER_STDEV = 2.0


def relative_strength_index(closes: pd.Series, period: int = RSI_PERIOD) -> float | None:
    """Wilder's RSI of the latest bar, or None when there is not enough history.

    Needs period+1 closes to form the first `period` price changes. A flat series
    (no losses) pins RSI at 100 rather than dividing by zero.
    """
    prices = _clean(closes)
    if len(prices) <= period:
        return None
    delta = prices.diff().dropna()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)
    avg_gain = _wilder_average(gains, period)
    avg_loss = _wilder_average(losses, period)
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def _wilder_average(series: pd.Series, period: int) -> float:
    """Seed with the simple mean of the first `period` values, then smooth."""
    seed = float(series.iloc[:period].mean())
    average = seed
    for value in series.iloc[period:]:
        average = (average * (period - 1) + float(value)) / period
    return average


def simple_moving_average(closes: pd.Series, window: int) -> float | None:
    prices = _clean(closes)
    if len(prices) < window:
        return None
    return float(prices.iloc[-window:].mean())


def moving_average_series(closes: pd.Series, window: int) -> pd.Series:
    """A chart needs the whole line; leading days stay NaN until the window fills."""
    return _clean(closes).rolling(window).mean()


@dataclass(frozen=True)
class BollingerBands:
    lower: float
    middle: float
    upper: float


def bollinger_bands(
    closes: pd.Series, window: int = BOLLINGER_WINDOW, num_std: float = BOLLINGER_STDEV
) -> BollingerBands | None:
    prices = _clean(closes)
    if len(prices) < window:
        return None
    recent = prices.iloc[-window:]
    middle = float(recent.mean())
    # Population std (ddof=0): the band is a description of this window, not an
    # inference about a wider sample.
    spread = float(recent.std(ddof=0)) * num_std
    return BollingerBands(lower=middle - spread, middle=middle, upper=middle + spread)


@dataclass(frozen=True)
class IndicatorSnapshot:
    """Everything the rule engine reads about one symbol's recent price action.

    Any field can be None when the history is too short for that indicator; the
    rule engine simply skips the conditions it cannot evaluate.
    """

    symbol: str
    last_close: float
    rsi: float | None
    sma_short: float | None
    sma_long: float | None
    bands: BollingerBands | None
    as_of: str | None = None
    week52_low: float | None = None
    week52_high: float | None = None

    @property
    def range_percentile(self) -> float | None:
        """Where the price sits inside its 52-week low–high, 0% = year low, 100% = high."""
        if self.week52_low is None or self.week52_high is None:
            return None
        span = self.week52_high - self.week52_low
        if span <= 0:
            return None
        return (self.last_close - self.week52_low) / span * 100

    @staticmethod
    def _bias(price: float, reference: float | None) -> float | None:
        """Percent the price sits above (+) or below (-) a reference line."""
        if not reference:
            return None
        return (price / reference - 1) * 100

    @property
    def bias_to_sma_short_percent(self) -> float | None:
        return self._bias(self.last_close, self.sma_short)

    @property
    def bias_to_sma_long_percent(self) -> float | None:
        return self._bias(self.last_close, self.sma_long)

    @property
    def _has_width(self) -> bool:
        """A zero-width band (a dead-flat window) describes nothing, so it never fires."""
        return self.bands is not None and self.bands.upper > self.bands.lower

    @property
    def below_lower_band(self) -> bool:
        return self._has_width and self.last_close <= self.bands.lower

    @property
    def above_upper_band(self) -> bool:
        return self._has_width and self.last_close >= self.bands.upper


def compute_indicators(
    symbol: str,
    closes: pd.Series,
    *,
    short_window: int = SHORT_TREND_WINDOW,
    long_window: int = LONG_TREND_WINDOW,
) -> IndicatorSnapshot | None:
    """None when there is no price data at all — that is 'unknown', not 'no signal'."""
    prices = _clean(closes)
    if prices.empty:
        return None
    year = prices.iloc[-TRADING_DAYS_PER_YEAR:]
    return IndicatorSnapshot(
        symbol=symbol,
        last_close=float(prices.iloc[-1]),
        rsi=relative_strength_index(prices),
        sma_short=simple_moving_average(prices, short_window),
        sma_long=simple_moving_average(prices, long_window),
        bands=bollinger_bands(prices),
        as_of=prices.index[-1].date().isoformat() if isinstance(prices.index, pd.DatetimeIndex) else None,
        week52_low=float(year.min()),
        week52_high=float(year.max()),
    )


def _clean(closes: pd.Series) -> pd.Series:
    """Drop gaps so a single missing day does not poison an average."""
    return closes.dropna()
