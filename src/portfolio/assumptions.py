from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import date, timedelta

from .historical_pe import HistoricalPriceEarnings, compute_historical_price_earnings
from .market import MarketDataClient
from .valuation import ValuationAssumptions

GLOBAL_DEFAULT_KEY = "*"
CACHE_MAX_AGE_DAYS = 30

SOURCE_MANUAL = "manual"
SOURCE_HISTORICAL = "historical"
SOURCE_DEFAULT = "default"

ASSUMPTION_FIELDS = ("target_pe", "required_return", "dividend_growth", "margin_of_safety")
FIELD_TO_ATTRIBUTE = {
    "target_pe": "target_price_earnings",
    "required_return": "required_return",
    "dividend_growth": "dividend_growth",
    "margin_of_safety": "margin_of_safety",
}


@dataclass(frozen=True)
class AssumptionOverride:
    """A partial override; None means "leave this one alone"."""

    symbol: str
    target_pe: float | None = None
    required_return: float | None = None
    dividend_growth: float | None = None
    margin_of_safety: float | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, field) is None for field in ASSUMPTION_FIELDS)

    def apply_to(self, base: ValuationAssumptions) -> ValuationAssumptions:
        changes = {
            FIELD_TO_ATTRIBUTE[field]: getattr(self, field)
            for field in ASSUMPTION_FIELDS
            if getattr(self, field) is not None
        }
        if "margin_of_safety" in changes:
            # A margin the user typed is theirs exactly; never widen it behind their back.
            changes["adaptive_margin"] = False
        return replace(base, **changes) if changes else base


@dataclass(frozen=True)
class ResolvedAssumptions:
    symbol: str
    assumptions: ValuationAssumptions
    source: str
    historical: HistoricalPriceEarnings | None = None

    @property
    def historical_median_pe(self) -> float | None:
        return self.historical.median_pe if self.historical else None


def save_override(connection: sqlite3.Connection, override: AssumptionOverride) -> None:
    existing = load_override(connection, override.symbol)
    merged = override if existing is None else _merge_overrides(existing, override)
    connection.execute(
        "INSERT OR REPLACE INTO valuation_assumptions VALUES (?, ?, ?, ?, ?)",
        (
            merged.symbol.upper(),
            merged.target_pe,
            merged.required_return,
            merged.dividend_growth,
            merged.margin_of_safety,
        ),
    )
    connection.commit()


def _merge_overrides(
    existing: AssumptionOverride, incoming: AssumptionOverride
) -> AssumptionOverride:
    values = {
        field: (
            getattr(incoming, field)
            if getattr(incoming, field) is not None
            else getattr(existing, field)
        )
        for field in ASSUMPTION_FIELDS
    }
    return AssumptionOverride(symbol=existing.symbol, **values)


def load_override(connection: sqlite3.Connection, symbol: str) -> AssumptionOverride | None:
    row = connection.execute(
        "SELECT * FROM valuation_assumptions WHERE symbol = ?", (symbol.upper(),)
    ).fetchone()
    return _row_to_override(row) if row else None


def _row_to_override(row: sqlite3.Row) -> AssumptionOverride:
    return AssumptionOverride(
        symbol=row["symbol"],
        target_pe=row["target_pe"],
        required_return=row["required_return"],
        dividend_growth=row["dividend_growth"],
        margin_of_safety=row["margin_of_safety"],
    )


def load_all_overrides(connection: sqlite3.Connection) -> list[AssumptionOverride]:
    rows = connection.execute(
        "SELECT * FROM valuation_assumptions WHERE symbol != ? ORDER BY symbol",
        (GLOBAL_DEFAULT_KEY,),
    ).fetchall()
    return [_row_to_override(row) for row in rows]


def delete_override(connection: sqlite3.Connection, symbol: str) -> bool:
    cursor = connection.execute(
        "DELETE FROM valuation_assumptions WHERE symbol = ?", (symbol.upper(),)
    )
    connection.commit()
    return cursor.rowcount > 0


def load_global_default(connection: sqlite3.Connection) -> ValuationAssumptions:
    override = load_override(connection, GLOBAL_DEFAULT_KEY)
    base = ValuationAssumptions()
    return override.apply_to(base) if override else base


def save_cached_historical(
    connection: sqlite3.Connection, historical: HistoricalPriceEarnings, computed_on: date
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO historical_pe_cache VALUES (?, ?, ?, ?, ?, ?)",
        (
            historical.symbol.upper(),
            historical.median_pe,
            historical.lower_quartile_pe,
            historical.upper_quartile_pe,
            historical.observation_days,
            computed_on.isoformat(),
        ),
    )
    connection.commit()


def load_cached_historical(
    connection: sqlite3.Connection, symbol: str, today: date
) -> HistoricalPriceEarnings | None:
    row = connection.execute(
        "SELECT * FROM historical_pe_cache WHERE symbol = ?", (symbol.upper(),)
    ).fetchone()
    if row is None or _is_stale(row["computed_on"], today):
        return None
    return HistoricalPriceEarnings(
        symbol=row["symbol"],
        median_pe=row["median_pe"],
        lower_quartile_pe=row["lower_quartile_pe"],
        upper_quartile_pe=row["upper_quartile_pe"],
        observation_days=row["observation_days"],
    )


def _is_stale(computed_on: str, today: date) -> bool:
    return date.fromisoformat(computed_on) < today - timedelta(days=CACHE_MAX_AGE_DAYS)


def get_historical_price_earnings(
    connection: sqlite3.Connection, client: MarketDataClient, symbol: str
) -> HistoricalPriceEarnings:
    """Cached for 30 days; annual EPS only changes a few times a year."""
    today = date.today()
    cached = load_cached_historical(connection, symbol, today)
    if cached is not None:
        return cached
    computed = compute_historical_price_earnings(client, symbol)
    save_cached_historical(connection, computed, today)
    return computed


def resolve_assumptions(
    connection: sqlite3.Connection, client: MarketDataClient, symbol: str
) -> ResolvedAssumptions:
    """Global default, then the stock's own historical P/E, then any manual override."""
    base = load_global_default(connection)
    historical = get_historical_price_earnings(connection, client, symbol)
    resolved, source = _apply_historical(base, historical)
    override = load_override(connection, symbol)
    if override is not None and not override.is_empty():
        resolved, source = override.apply_to(resolved), SOURCE_MANUAL
    return ResolvedAssumptions(symbol.upper(), resolved, source, historical)


def _apply_historical(
    base: ValuationAssumptions, historical: HistoricalPriceEarnings
) -> tuple[ValuationAssumptions, str]:
    if not (historical.is_usable and historical.is_stable):
        return base, SOURCE_DEFAULT
    return replace(base, target_price_earnings=historical.median_pe), SOURCE_HISTORICAL
