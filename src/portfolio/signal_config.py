from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace

from .signals import SignalThresholds

GLOBAL_DEFAULT_KEY = "*"
SOURCE_MANUAL = "manual"
SOURCE_DEFAULT = "default"

THRESHOLD_FIELDS = (
    "average_cost_discount_percent",
    "short_trend_bias_percent",
    "rsi_oversold",
    "rsi_overbought",
)


@dataclass(frozen=True)
class ThresholdOverride:
    """A partial threshold override; None means "leave this rule at the level below"."""

    symbol: str
    average_cost_discount_percent: float | None = None
    short_trend_bias_percent: float | None = None
    rsi_oversold: float | None = None
    rsi_overbought: float | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, field) is None for field in THRESHOLD_FIELDS)

    def apply_to(self, base: SignalThresholds) -> SignalThresholds:
        changes = {
            field: getattr(self, field)
            for field in THRESHOLD_FIELDS
            if getattr(self, field) is not None
        }
        return replace(base, **changes) if changes else base


def save_threshold_override(connection: sqlite3.Connection, override: ThresholdOverride) -> None:
    existing = load_threshold_override(connection, override.symbol)
    merged = override if existing is None else _merge(existing, override)
    connection.execute(
        "INSERT OR REPLACE INTO signal_thresholds VALUES (?, ?, ?, ?, ?)",
        (
            merged.symbol.upper(),
            merged.average_cost_discount_percent,
            merged.short_trend_bias_percent,
            merged.rsi_oversold,
            merged.rsi_overbought,
        ),
    )
    connection.commit()


def _merge(existing: ThresholdOverride, incoming: ThresholdOverride) -> ThresholdOverride:
    values = {
        field: (
            getattr(incoming, field)
            if getattr(incoming, field) is not None
            else getattr(existing, field)
        )
        for field in THRESHOLD_FIELDS
    }
    return ThresholdOverride(symbol=existing.symbol, **values)


def load_threshold_override(
    connection: sqlite3.Connection, symbol: str
) -> ThresholdOverride | None:
    row = connection.execute(
        "SELECT * FROM signal_thresholds WHERE symbol = ?", (symbol.upper(),)
    ).fetchone()
    return _row_to_override(row) if row else None


def _row_to_override(row: sqlite3.Row) -> ThresholdOverride:
    return ThresholdOverride(
        symbol=row["symbol"],
        average_cost_discount_percent=row["average_cost_discount_percent"],
        short_trend_bias_percent=row["short_trend_bias_percent"],
        rsi_oversold=row["rsi_oversold"],
        rsi_overbought=row["rsi_overbought"],
    )


def load_all_threshold_overrides(connection: sqlite3.Connection) -> list[ThresholdOverride]:
    rows = connection.execute(
        "SELECT * FROM signal_thresholds WHERE symbol != ? ORDER BY symbol",
        (GLOBAL_DEFAULT_KEY,),
    ).fetchall()
    return [_row_to_override(row) for row in rows]


def delete_threshold_override(connection: sqlite3.Connection, symbol: str) -> bool:
    cursor = connection.execute(
        "DELETE FROM signal_thresholds WHERE symbol = ?", (symbol.upper(),)
    )
    connection.commit()
    return cursor.rowcount > 0


def load_global_thresholds(connection: sqlite3.Connection) -> SignalThresholds:
    override = load_threshold_override(connection, GLOBAL_DEFAULT_KEY)
    base = SignalThresholds()
    return override.apply_to(base) if override else base


@dataclass(frozen=True)
class ResolvedThresholds:
    symbol: str
    thresholds: SignalThresholds
    source: str


def resolve_thresholds(connection: sqlite3.Connection, symbol: str) -> ResolvedThresholds:
    """Built-in defaults, then the global override, then this symbol's own override."""
    base = load_global_thresholds(connection)
    override = load_threshold_override(connection, symbol)
    if override is not None and not override.is_empty():
        return ResolvedThresholds(symbol.upper(), override.apply_to(base), SOURCE_MANUAL)
    return ResolvedThresholds(symbol.upper(), base, SOURCE_DEFAULT)
