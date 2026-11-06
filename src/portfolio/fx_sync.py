from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .config import Settings
from .currency import (
    RATE_LOOKBACK_DAYS,
    pair_name,
    pair_symbol,
    rate_on_or_before,
    rate_series_to_lookup,
)
from .firstrade_csv import BrokerageTransaction
from .market import MarketDataClient
from .repository import (
    count_transactions_without_fx,
    load_fx_rates,
    load_transactions,
    save_fx_rates,
    update_transaction_fx_rates,
)
from .store import open_store


@dataclass(frozen=True)
class FxBackfillOutcome:
    pair: str
    filled: int
    still_missing: int
    rates_cached: int

    @property
    def is_complete(self) -> bool:
        return self.still_missing == 0


def _transactions_needing_rates(
    transactions: list[BrokerageTransaction],
) -> list[BrokerageTransaction]:
    return [item for item in transactions if item.fx_rate is None]


def _required_date_range(pending: list[BrokerageTransaction]) -> tuple[date, date]:
    days = [item.trade_date for item in pending]
    return min(days) - timedelta(days=RATE_LOOKBACK_DAYS), max(days) + timedelta(days=1)


def _fetch_and_cache(
    settings: Settings, client: MarketDataClient, window: tuple[date, date]
) -> dict[str, float]:
    pair = pair_name(settings.home_currency)
    series = client.exchange_rate_history(pair_symbol(settings.home_currency), *window)
    fetched = rate_series_to_lookup(series)
    with open_store(settings.database_path) as connection:
        if fetched:
            save_fx_rates(connection, pair, fetched)
        return load_fx_rates(connection, pair)


def backfill_transaction_fx(settings: Settings, client: MarketDataClient) -> FxBackfillOutcome:
    """Stamps each trade with the rate on its own trade date, not today's rate."""
    pair = pair_name(settings.home_currency)
    with open_store(settings.database_path) as connection:
        pending = _transactions_needing_rates(load_transactions(connection))
    if not pending:
        return FxBackfillOutcome(pair, 0, 0, 0)
    rates = _fetch_and_cache(settings, client, _required_date_range(pending))
    resolved = _resolve_rates(pending, rates)
    with open_store(settings.database_path) as connection:
        update_transaction_fx_rates(connection, resolved)
        missing = count_transactions_without_fx(connection)
    return FxBackfillOutcome(pair, len(resolved), missing, len(rates))


def _resolve_rates(
    pending: list[BrokerageTransaction], rates: dict[str, float]
) -> dict[str, float]:
    resolved: dict[str, float] = {}
    for item in pending:
        rate = rate_on_or_before(rates, item.trade_date)
        if rate is not None:
            resolved[item.row_fingerprint] = rate
    return resolved
