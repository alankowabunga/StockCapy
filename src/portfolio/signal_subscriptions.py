"""Which symbols the smart alert pushes for. Holdings and the watchlist are in by
default; a stored row only records the user's exceptions: muting one of them, or
following a symbol that is neither held nor watched (decisions.md D29)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

SOURCE_HOLDING = "持股"
SOURCE_WATCHLIST = "監控"
SOURCE_FOLLOWED = "手動加入"
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")


class InvalidSymbolError(ValueError):
    pass


@dataclass(frozen=True)
class SignalSubscription:
    symbol: str
    sources: tuple[str, ...]
    is_enabled: bool


@dataclass(frozen=True)
class DefaultScope:
    """The symbols that are in by default, before any stored exception."""

    held: frozenset[str]
    watched: frozenset[str]

    def includes(self, symbol: str) -> bool:
        return symbol in self.held or symbol in self.watched


def normalize_symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    if not _SYMBOL_PATTERN.match(symbol):
        raise InvalidSymbolError(f"不是有效的股票代號：{raw!r}")
    return symbol


def resolve_subscriptions(
    scope: DefaultScope, overrides: dict[str, bool]
) -> list[SignalSubscription]:
    followed = {symbol for symbol, enabled in overrides.items() if enabled}
    symbols = sorted(scope.held | scope.watched | followed)
    return [
        SignalSubscription(symbol, _sources(symbol, scope), overrides.get(symbol, True))
        for symbol in symbols
    ]


def _sources(symbol: str, scope: DefaultScope) -> tuple[str, ...]:
    sources = []
    if symbol in scope.held:
        sources.append(SOURCE_HOLDING)
    if symbol in scope.watched:
        sources.append(SOURCE_WATCHLIST)
    return tuple(sources) or (SOURCE_FOLLOWED,)


def store_subscription_choice(
    connection: sqlite3.Connection, scope: DefaultScope, symbol: str, enabled: bool
) -> None:
    """Keep only real exceptions: turning a default symbol back on, or turning off a
    followed-only one, removes the row instead of storing a no-op."""
    if enabled == scope.includes(symbol):
        delete_subscription(connection, symbol)
        return
    connection.execute(
        "INSERT OR REPLACE INTO signal_subscriptions (symbol, enabled, updated_at) VALUES (?, ?, ?)",
        (symbol, int(enabled), datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    connection.commit()


def delete_subscription(connection: sqlite3.Connection, symbol: str) -> None:
    connection.execute("DELETE FROM signal_subscriptions WHERE symbol = ?", (symbol,))
    connection.commit()


def load_subscription_overrides(connection: sqlite3.Connection) -> dict[str, bool]:
    rows = connection.execute("SELECT symbol, enabled FROM signal_subscriptions").fetchall()
    return {row["symbol"]: bool(row["enabled"]) for row in rows}
