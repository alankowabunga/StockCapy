from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from .firstrade_csv import BrokerageTransaction
from .holdings import Position
from .market import MarketDataClient


@dataclass(frozen=True)
class DividendReceipt:
    """What actually landed in the account, per the broker's own records."""

    symbol: str
    gross_amount: float
    withheld_amount: float

    @property
    def net_amount(self) -> float:
        return self.gross_amount - self.withheld_amount

    @property
    def effective_withholding_percent(self) -> float:
        if not self.gross_amount:
            return 0.0
        return self.withheld_amount / self.gross_amount * 100


TRAILING_YEAR_DAYS = 365


@dataclass(frozen=True)
class ForwardDividendForecast:
    symbol: str
    quantity: float
    annual_rate_per_share: float
    withholding_rate: float

    @property
    def gross_annual_income(self) -> float:
        return self.quantity * self.annual_rate_per_share

    @property
    def net_annual_income(self) -> float:
        return self.gross_annual_income * (1 - self.withholding_rate)


def summarize_received_dividends(
    transactions: list[BrokerageTransaction],
) -> dict[str, DividendReceipt]:
    """Handles both Firstrade layouts: tax embedded in the dividend row, or as its own row."""
    gross: dict[str, float] = {}
    withheld: dict[str, float] = {}
    for transaction in transactions:
        symbol = transaction.symbol
        if transaction.activity == "DIVIDEND":
            gross[symbol] = gross.get(symbol, 0.0) + transaction.cash_amount
            withheld[symbol] = withheld.get(symbol, 0.0) + transaction.withheld_amount
        elif transaction.activity == "WITHHOLDING":
            withheld[symbol] = withheld.get(symbol, 0.0) + abs(transaction.cash_amount)
    symbols = sorted(set(gross) | set(withheld))
    return {
        symbol: DividendReceipt(symbol, gross.get(symbol, 0.0), withheld.get(symbol, 0.0))
        for symbol in symbols
    }


def trailing_twelve_month_dividend(client: MarketDataClient, symbol: str) -> float:
    """ETFs often lack a declared dividendRate, so fall back to what was actually paid."""
    history = client.dividend_history(symbol)
    if history.empty:
        return 0.0
    index = pd.to_datetime(history.index).tz_localize(None)
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=TRAILING_YEAR_DAYS)
    return float(history.set_axis(index)[index >= cutoff].sum())


def resolve_annual_dividend_rate(client: MarketDataClient, symbol: str) -> float:
    declared = client.fundamentals(symbol).number("dividendRate") or 0.0
    return declared if declared > 0 else trailing_twelve_month_dividend(client, symbol)


def forecast_dividends(
    client: MarketDataClient, positions: list[Position], withholding_rate: float
) -> list[ForwardDividendForecast]:
    forecasts = []
    for position in positions:
        annual_rate = resolve_annual_dividend_rate(client, position.symbol)
        if annual_rate <= 0:
            continue
        forecasts.append(
            ForwardDividendForecast(
                symbol=position.symbol,
                quantity=position.quantity,
                annual_rate_per_share=annual_rate,
                withholding_rate=withholding_rate,
            )
        )
    return sorted(forecasts, key=lambda item: -item.gross_annual_income)


def dividends_paid_since(
    client: MarketDataClient, symbol: str, since: date
) -> pd.Series:
    history = client.dividend_history(symbol)
    if history.empty:
        return history
    index = pd.to_datetime(history.index).tz_localize(None)
    return history.set_axis(index)[index >= pd.Timestamp(since)]
