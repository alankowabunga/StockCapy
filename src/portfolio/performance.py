from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .currency import CurrencyAttribution
from .holdings import SHARE_EPSILON, Position
from .market import MarketDataClient, Quote
from .markets import currency_for_symbol, is_taiwan_symbol

TRADING_DAYS_PER_YEAR = 252
BENCHMARK_SYMBOL = "SPY"

# Floating-point noise makes a constant series report std ~1e-19 rather than 0,
# which would otherwise turn a flat portfolio into an astronomical Sharpe ratio.
MIN_MEANINGFUL_STDDEV = 1e-12


class ExchangeRateUnavailableError(RuntimeError):
    """A Taiwan holding cannot be totalled with US ones without the USD/TWD rate."""


@dataclass(frozen=True)
class PositionPerformance:
    symbol: str
    quantity: float
    average_cost: float
    last_price: float
    sector: str
    average_cost_rate: float | None = None
    rate_now: float | None = None
    unpriced_quantity: float = 0.0
    """Shares a dividend proves are held but whose purchase price the import never saw.
    They count toward market value and weight; any figure needing a cost is withheld."""
    usd_per_native: float = 1.0
    """Converts this holding's own currency into USD, the unit every portfolio total is
    kept in (D31). Always 1.0 for US symbols, so US-only portfolios compute as before."""
    home_is_native: bool = False
    """True when the home currency IS this holding's currency (a Taiwan stock with TWD
    home): its cost is already in home money, so there is no FX effect to split out."""

    @property
    def currency(self) -> str:
        return currency_for_symbol(self.symbol)

    @property
    def is_taiwan(self) -> bool:
        return is_taiwan_symbol(self.symbol)

    @property
    def market_value_usd(self) -> float:
        return self.market_value * self.usd_per_native

    @property
    def cost_basis_usd(self) -> float:
        return self.cost_basis * self.usd_per_native

    @property
    def total_quantity(self) -> float:
        return self.quantity + self.unpriced_quantity

    @property
    def has_complete_cost_basis(self) -> bool:
        return self.unpriced_quantity <= SHARE_EPSILON

    @property
    def cost_basis_home(self) -> float | None:
        if self.home_is_native:
            return self.cost_basis
        if self.average_cost_rate is None:
            return None
        return self.cost_basis * self.average_cost_rate

    @property
    def attribution(self) -> CurrencyAttribution | None:
        """None until the position's trades carry FX rates, and none while shares are
        unpriced — the attribution splits a gain that cannot be computed yet."""
        if not self.has_complete_cost_basis:
            return None
        if self.home_is_native:
            return self._native_attribution()
        if self.is_taiwan:
            return None
        if self.average_cost_rate is None or not self.rate_now:
            return None
        return CurrencyAttribution(
            cost_base=self.cost_basis,
            cost_home=self.cost_basis * self.average_cost_rate,
            market_value_base=self.market_value,
            rate_now=self.rate_now,
        )

    def _native_attribution(self) -> CurrencyAttribution | None:
        """Home money equals the holding's own money, so the whole gain is the stock's:
        expressing cost and value in USD at today's rate makes the FX part exactly zero."""
        if not self.usd_per_native:
            return None
        return CurrencyAttribution(
            cost_base=self.cost_basis_usd,
            cost_home=self.cost_basis,
            market_value_base=self.market_value_usd,
            rate_now=1 / self.usd_per_native,
        )

    @property
    def market_value(self) -> float:
        return self.total_quantity * self.last_price

    @property
    def cost_basis(self) -> float:
        """Only the shares whose price is known — never an estimate for the rest."""
        return self.quantity * self.average_cost

    @property
    def unrealized_gain(self) -> float | None:
        """None while shares are unpriced: subtracting a partial cost from a full market
        value would report the missing block's whole value as profit."""
        if not self.has_complete_cost_basis:
            return None
        return self.market_value - self.cost_basis

    @property
    def unrealized_return_percent(self) -> float | None:
        gain = self.unrealized_gain
        if gain is None:
            return None
        if not self.cost_basis:
            return 0.0
        return gain / self.cost_basis * 100


@dataclass(frozen=True)
class RiskMetrics:
    annualized_volatility_percent: float
    max_drawdown_percent: float
    sharpe_ratio: float
    benchmark_correlation: float
    observation_days: int


@dataclass
class PortfolioPerformance:
    positions: list[PositionPerformance]
    realized_gain: float

    @property
    def total_market_value(self) -> float:
        """In USD: Taiwan holdings are converted at today's rate, so US-only portfolios
        are unchanged and snapshots keep one unit (D31)."""
        return sum(position.market_value_usd for position in self.positions)

    @property
    def total_cost_basis(self) -> float:
        """Sums only the known costs, so it pairs with total_market_value only when
        has_complete_cost_basis is true."""
        return sum(position.cost_basis_usd for position in self.positions)

    @property
    def has_taiwan_positions(self) -> bool:
        return any(position.is_taiwan for position in self.positions)

    @property
    def usd_to_twd(self) -> float | None:
        """TWD per USD as used for the Taiwan holdings; None without any."""
        for position in self.positions:
            if position.is_taiwan and position.usd_per_native:
                return 1 / position.usd_per_native
        return None

    def market_subtotal(self, taiwan: bool) -> tuple[float, float]:
        """(market value, known cost) in that market's own currency."""
        chosen = [p for p in self.positions if p.is_taiwan == taiwan]
        return (
            sum(p.market_value for p in chosen),
            sum(p.cost_basis for p in chosen),
        )

    @property
    def total_market_value_twd(self) -> float | None:
        rate = self.usd_to_twd
        return None if rate is None else self.total_market_value * rate

    @property
    def total_cost_basis_twd(self) -> float | None:
        rate = self.usd_to_twd
        return None if rate is None else self.total_cost_basis * rate

    @property
    def has_complete_cost_basis(self) -> bool:
        return all(position.has_complete_cost_basis for position in self.positions)

    @property
    def total_unrealized_gain(self) -> float | None:
        if not self.has_complete_cost_basis:
            return None
        return self.total_market_value - self.total_cost_basis

    @property
    def total_return_percent(self) -> float | None:
        gain = self.total_unrealized_gain
        if gain is None:
            return None
        if not self.total_cost_basis:
            return 0.0
        return gain / self.total_cost_basis * 100

    @property
    def positions_missing_cost_basis(self) -> list[PositionPerformance]:
        return [p for p in self.positions if not p.has_complete_cost_basis]

    def weight_of(self, position: PositionPerformance) -> float:
        total = self.total_market_value
        return position.market_value_usd / total * 100 if total else 0.0

    def contribution_of(self, position: PositionPerformance) -> float | None:
        """Each holding's share of total unrealized gain, in currency terms."""
        return position.unrealized_gain

    @property
    def attributions(self) -> list[CurrencyAttribution]:
        found = (position.attribution for position in self.positions)
        return [item for item in found if item is not None]

    @property
    def total_cost_home(self) -> float:
        return sum(item.cost_home for item in self.attributions)

    @property
    def total_market_value_home(self) -> float:
        return sum(item.market_value_home for item in self.attributions)

    @property
    def total_stock_gain_home(self) -> float:
        return sum(item.stock_gain_home for item in self.attributions)

    @property
    def total_currency_gain_home(self) -> float:
        return sum(item.currency_gain_home for item in self.attributions)

    @property
    def total_gain_home(self) -> float:
        return self.total_stock_gain_home + self.total_currency_gain_home

    @property
    def total_return_home_percent(self) -> float:
        cost = self.total_cost_home
        return self.total_gain_home / cost * 100 if cost else 0.0

    @property
    def has_home_currency_data(self) -> bool:
        return bool(self.attributions)

    def exposure_by_sector(self) -> dict[str, float]:
        exposure: dict[str, float] = {}
        for position in self.positions:
            label = position.sector or "Unknown"
            exposure[label] = exposure.get(label, 0.0) + self.weight_of(position)
        return dict(sorted(exposure.items(), key=lambda item: -item[1]))


def build_position_performance(
    position: Position,
    quote: Quote,
    sector: str,
    rate_now: float | None = None,
    usd_to_twd: float | None = None,
    home_currency: str = "USD",
) -> PositionPerformance:
    if is_taiwan_symbol(position.symbol):
        return _taiwan_position_performance(
            position, quote, sector, usd_to_twd, home_currency
        )
    return PositionPerformance(
        symbol=position.symbol,
        quantity=position.quantity,
        average_cost=position.average_cost,
        last_price=quote.last_price,
        sector=sector,
        average_cost_rate=position.average_cost_rate,
        rate_now=rate_now,
        unpriced_quantity=position.unpriced_quantity,
    )


def _taiwan_position_performance(
    position: Position,
    quote: Quote,
    sector: str,
    usd_to_twd: float | None,
    home_currency: str,
) -> PositionPerformance:
    """Cost is in TWD, never FX-converted at purchase, so only today's rate is needed to
    put it beside US holdings. Without a rate nothing can be added up honestly."""
    if not usd_to_twd:
        raise ExchangeRateUnavailableError(
            "持倉含台股，需要 USD/TWD 匯率才能和美股合計，但目前取不到匯率"
        )
    return PositionPerformance(
        symbol=position.symbol,
        quantity=position.quantity,
        average_cost=position.average_cost,
        last_price=quote.last_price,
        sector=sector,
        average_cost_rate=None,
        rate_now=usd_to_twd,
        unpriced_quantity=position.unpriced_quantity,
        usd_per_native=1 / usd_to_twd,
        home_is_native=home_currency.upper() == "TWD",
    )


def _weighted_return_series(
    price_frame: pd.DataFrame, weights: dict[str, float]
) -> pd.Series:
    daily_returns = price_frame.pct_change(fill_method=None).dropna(how="all")
    aligned_weights = pd.Series(weights).reindex(daily_returns.columns).fillna(0.0)
    if aligned_weights.sum() == 0:
        return pd.Series(dtype="float64")
    normalized = aligned_weights / aligned_weights.sum()
    return (daily_returns.fillna(0.0) * normalized).sum(axis=1)


def max_drawdown_percent(return_series: pd.Series) -> float:
    if return_series.empty:
        return 0.0
    cumulative = (1 + return_series).cumprod()
    running_peak = cumulative.cummax()
    return float((cumulative / running_peak - 1).min() * 100)


def sharpe_ratio(return_series: pd.Series, risk_free_rate: float = 0.04) -> float:
    if return_series.empty:
        return 0.0
    daily_stddev = float(return_series.std())
    if daily_stddev < MIN_MEANINGFUL_STDDEV:
        return 0.0
    excess_daily = return_series.mean() - risk_free_rate / TRADING_DAYS_PER_YEAR
    return float(excess_daily / daily_stddev * np.sqrt(TRADING_DAYS_PER_YEAR))


def _fetch_price_frame(
    client: MarketDataClient, symbols: list[str], start: date, end: date
) -> pd.DataFrame:
    series_by_symbol = {}
    for symbol in symbols:
        closes = client.close_prices(symbol, start, end)
        if not closes.empty:
            series_by_symbol[symbol] = closes
    return pd.DataFrame(series_by_symbol) if series_by_symbol else pd.DataFrame()


def compute_risk_metrics(
    client: MarketDataClient,
    performance: PortfolioPerformance,
    lookback_days: int = 365,
) -> RiskMetrics | None:
    """Risk of TODAY's holdings over the lookback window, not of past trading."""
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    weights = {p.symbol: p.market_value_usd for p in performance.positions}
    price_frame = _fetch_price_frame(client, [*weights, BENCHMARK_SYMBOL], start, end)
    if price_frame.empty or BENCHMARK_SYMBOL not in price_frame:
        return None
    benchmark_returns = price_frame[BENCHMARK_SYMBOL].pct_change(fill_method=None).dropna()
    portfolio_returns = _weighted_return_series(price_frame.drop(columns=[BENCHMARK_SYMBOL]), weights)
    if portfolio_returns.empty:
        return None
    return _assemble_risk_metrics(portfolio_returns, benchmark_returns)


def _assemble_risk_metrics(
    portfolio_returns: pd.Series, benchmark_returns: pd.Series
) -> RiskMetrics:
    correlation = portfolio_returns.corr(benchmark_returns)
    return RiskMetrics(
        annualized_volatility_percent=float(
            portfolio_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR) * 100
        ),
        max_drawdown_percent=max_drawdown_percent(portfolio_returns),
        sharpe_ratio=sharpe_ratio(portfolio_returns),
        benchmark_correlation=float(correlation) if pd.notna(correlation) else 0.0,
        observation_days=len(portfolio_returns),
    )
