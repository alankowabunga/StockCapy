"""Which exchange a symbol trades on, read from its Yahoo suffix (D30).

Only two markets exist here: Taiwan (`.TW` listed, `.TWO` OTC) and everything else,
treated as US. The suffix is the single source of truth, so no table has to remember a
currency per holding."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import time

TAIWAN_SUFFIXES = (".TW", ".TWO")
_BARE_TAIWAN_CODE = re.compile(r"^\d{4,6}[A-Z]?$")


class InvalidSymbolError(ValueError):
    pass


@dataclass(frozen=True)
class Market:
    code: str
    label: str
    currency: str
    close_utc: time
    """Late enough to be right all year: US 16:00 ET is 20:00/21:00 UTC; Taiwan 13:30
    is 05:30 UTC with no daylight saving."""


US_MARKET = Market(code="US", label="美股", currency="USD", close_utc=time(21, 0))
TW_MARKET = Market(code="TW", label="台股", currency="TWD", close_utc=time(5, 30))


def market_for_symbol(symbol: str) -> Market:
    return TW_MARKET if is_taiwan_symbol(symbol) else US_MARKET


def is_taiwan_symbol(symbol: str) -> bool:
    return (symbol or "").upper().endswith(TAIWAN_SUFFIXES)


def currency_for_symbol(symbol: str) -> str:
    return market_for_symbol(symbol).currency


def resolve_symbol_input(raw: str, has_price: Callable[[str], bool] | None) -> str:
    """A bare Taiwan code like 2330 gets .TW (listed) or .TWO (OTC), whichever has a
    price; anything else passes through uppercased."""
    symbol = (raw or "").strip().upper()
    if not _BARE_TAIWAN_CODE.match(symbol):
        return symbol
    if has_price is None:
        raise InvalidSymbolError(f"台股代號請加上 .TW（上市）或 .TWO（上櫃），例如 {symbol}.TW")
    for suffix in TAIWAN_SUFFIXES:
        if has_price(symbol + suffix):
            return symbol + suffix
    raise InvalidSymbolError(f"上市（.TW）與上櫃（.TWO）都找不到代號 {symbol}")
