from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import pandas as pd
import yfinance as yf

FUNDAMENTAL_FIELDS = (
    "trailingEps",
    "forwardEps",
    "bookValue",
    "trailingPE",
    "forwardPE",
    "dividendRate",
    "dividendYield",
    "targetMeanPrice",
    "numberOfAnalystOpinions",
    "sector",
    "industry",
    "currency",
    "financialCurrency",
    "freeCashflow",
    "sharesOutstanding",
    "earningsGrowth",
    "beta",
    "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow",
    "quoteType",
)

TICKER_CACHE_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class Quote:
    symbol: str
    last_price: float
    previous_close: float
    currency: str

    @property
    def daily_change_percent(self) -> float:
        if not self.previous_close:
            return 0.0
        return (self.last_price / self.previous_close - 1) * 100


@dataclass(frozen=True)
class PricePosition:
    """Where the price sits in its own recent history. Says nothing about intrinsic value."""

    symbol: str
    last_price: float
    week52_low: float
    week52_high: float
    two_hundred_day_average: float
    quote_type: str

    @property
    def is_fund(self) -> bool:
        return self.quote_type.upper() in {"ETF", "MUTUALFUND"}

    @property
    def range_percentile(self) -> float | None:
        span = self.week52_high - self.week52_low
        if span <= 0:
            return None
        return (self.last_price - self.week52_low) / span * 100

    @property
    def premium_to_two_hundred_day_percent(self) -> float | None:
        if not self.two_hundred_day_average:
            return None
        return (self.last_price / self.two_hundred_day_average - 1) * 100


@dataclass(frozen=True)
class Fundamentals:
    symbol: str
    values: dict[str, object]

    @property
    def is_empty(self) -> bool:
        """A failed or rate-limited lookup returns every field as None."""
        return all(value is None for value in self.values.values())

    def number(self, field_name: str) -> float | None:
        raw = self.values.get(field_name)
        return float(raw) if isinstance(raw, (int, float)) else None

    def text(self, field_name: str) -> str:
        raw = self.values.get(field_name)
        return str(raw) if raw else ""


@dataclass(frozen=True)
class _CachedTicker:
    ticker: yf.Ticker
    created_at: float


class MarketDataClient:
    """Thin yfinance wrapper. yfinance memoises prices on each Ticker forever, so cached
    Tickers expire after a TTL; otherwise a long-running server reports frozen quotes."""

    def __init__(
        self,
        ttl_seconds: float = TICKER_CACHE_TTL_SECONDS,
        ticker_factory: Callable[[str], yf.Ticker] = yf.Ticker,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._ticker_factory = ticker_factory
        self._clock = clock
        self._ticker_cache: dict[str, _CachedTicker] = {}

    def _ticker(self, symbol: str) -> yf.Ticker:
        now = self._clock()
        cached = self._ticker_cache.get(symbol)
        if cached is None or now - cached.created_at >= self._ttl_seconds:
            cached = _CachedTicker(ticker=self._ticker_factory(symbol), created_at=now)
            self._ticker_cache[symbol] = cached
        return cached.ticker

    def quote(self, symbol: str) -> Quote:
        fast_info = self._ticker(symbol).fast_info
        last_price = float(fast_info.get("lastPrice") or 0.0)
        previous_close = float(fast_info.get("previousClose") or last_price)
        return Quote(
            symbol=symbol,
            last_price=last_price,
            previous_close=previous_close,
            currency=str(fast_info.get("currency") or "USD"),
        )

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {symbol: self.quote(symbol) for symbol in symbols}

    def fundamentals(self, symbol: str) -> Fundamentals:
        """Retries once on an empty payload; a rate-limited lookup must not look like a
        company with no fundamentals."""
        result = self._fetch_fundamentals(symbol)
        if result.is_empty:
            self._ticker_cache.pop(symbol, None)
            result = self._fetch_fundamentals(symbol)
        return result

    def _fetch_fundamentals(self, symbol: str) -> Fundamentals:
        try:
            info = self._ticker(symbol).info or {}
        except Exception:  # noqa: BLE001 - any transport error means "no data right now"
            info = {}
        return Fundamentals(
            symbol=symbol, values={name: info.get(name) for name in FUNDAMENTAL_FIELDS}
        )

    def price_position(self, symbol: str) -> PricePosition:
        info = self._ticker(symbol).info or {}
        fast_info = self._ticker(symbol).fast_info
        return PricePosition(
            symbol=symbol,
            last_price=float(fast_info.get("lastPrice") or 0.0),
            week52_low=float(info.get("fiftyTwoWeekLow") or 0.0),
            week52_high=float(info.get("fiftyTwoWeekHigh") or 0.0),
            two_hundred_day_average=float(fast_info.get("twoHundredDayAverage") or 0.0),
            quote_type=str(info.get("quoteType") or fast_info.get("quoteType") or ""),
        )

    def exchange_rate_history(self, pair_symbol: str, start: date, end: date) -> pd.Series:
        frame = self._ticker(pair_symbol).history(start=start, end=end, auto_adjust=False)
        return frame["Close"] if not frame.empty else pd.Series(dtype="float64")

    def latest_exchange_rate(self, pair_symbol: str) -> float | None:
        rate = self._ticker(pair_symbol).fast_info.get("lastPrice")
        return float(rate) if rate else None

    def annual_diluted_eps(self, symbol: str) -> dict:
        """Fiscal-year-end date -> diluted EPS. Empty for funds, which report none."""
        statement = self._ticker(symbol).income_stmt
        if statement is None or statement.empty or "Diluted EPS" not in statement.index:
            return {}
        reported = statement.loc["Diluted EPS"].dropna()
        return {
            pd.Timestamp(period).tz_localize(None).normalize(): float(value)
            for period, value in reported.items()
        }

    def dividend_history(self, symbol: str) -> pd.Series:
        dividends = self._ticker(symbol).dividends
        return dividends if dividends is not None else pd.Series(dtype="float64")

    def close_prices(self, symbol: str, start: date, end: date) -> pd.Series:
        frame = self._ticker(symbol).history(start=start, end=end, auto_adjust=True)
        if frame.empty:
            return pd.Series(dtype="float64")
        closes = frame["Close"]
        closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
        return closes
