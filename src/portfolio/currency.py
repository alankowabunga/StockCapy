from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

BASE_CURRENCY = "USD"

# Taiwan writes the New Taiwan Dollar as NT$; a bare "$" reads as USD and misleads.
CURRENCY_SYMBOLS = {
    "TWD": "NT$",
    "USD": "$",
    "JPY": "¥",
    "EUR": "€",
    "GBP": "£",
    "HKD": "HK$",
    "CNY": "CN¥",
}
# FX markets close at weekends and holidays, so a trade date may have no quote of
# its own; widening the window lets the caller forward-fill from the prior session.
RATE_LOOKBACK_DAYS = 10


def currency_symbol(code: str) -> str:
    """Falls back to the code itself so an unmapped currency is still unambiguous."""
    normalized = (code or "").upper()
    return CURRENCY_SYMBOLS.get(normalized, f"{normalized} ")


def format_money(amount: float, currency: str) -> str:
    return f"{currency_symbol(currency)}{amount:,.2f}"


def labeled_money(amount: float, currency: str) -> str:
    """US output keeps its long-standing "USD $" label; others use their own sign."""
    if (currency or "").upper() == "USD":
        return f"USD ${amount:,.2f}"
    return format_money(amount, currency)


def pair_symbol(quote_currency: str, base_currency: str = BASE_CURRENCY) -> str:
    """Yahoo names the USD/TWD cross 'USDTWD=X'."""
    return f"{base_currency.upper()}{quote_currency.upper()}=X"


def pair_name(quote_currency: str, base_currency: str = BASE_CURRENCY) -> str:
    return f"{base_currency.upper()}{quote_currency.upper()}"


@dataclass(frozen=True)
class CurrencyAttribution:
    """Splits a position's home-currency gain into stock move and FX move.

    The two parts sum exactly to total_gain: proof is in the tests.
    """

    cost_base: float
    cost_home: float
    market_value_base: float
    rate_now: float

    @property
    def average_cost_rate(self) -> float | None:
        if not self.cost_base:
            return None
        return self.cost_home / self.cost_base

    @property
    def market_value_home(self) -> float:
        return self.market_value_base * self.rate_now

    @property
    def total_gain_home(self) -> float:
        return self.market_value_home - self.cost_home

    @property
    def stock_gain_home(self) -> float:
        """What the share price did, valued at today's rate."""
        return (self.market_value_base - self.cost_base) * self.rate_now

    @property
    def currency_gain_home(self) -> float:
        """What the exchange rate did to the money already invested."""
        average = self.average_cost_rate
        if average is None:
            return 0.0
        return self.cost_base * (self.rate_now - average)

    @property
    def total_return_percent(self) -> float:
        if not self.cost_home:
            return 0.0
        return self.total_gain_home / self.cost_home * 100


def rate_series_to_lookup(series: pd.Series) -> dict[str, float]:
    if series.empty:
        return {}
    index = pd.to_datetime(series.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    normalized = series.set_axis(index.normalize())
    return {day.date().isoformat(): float(value) for day, value in normalized.items()}


def rate_on_or_before(rates: dict[str, float], wanted: date) -> float | None:
    """Falls back to the most recent earlier session, for weekends and holidays."""
    for offset in range(RATE_LOOKBACK_DAYS + 1):
        candidate = (wanted - timedelta(days=offset)).isoformat()
        if candidate in rates:
            return rates[candidate]
    return None
