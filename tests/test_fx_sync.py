from datetime import date

import pandas as pd
import pytest
from conftest import make_settings, transaction

from portfolio.fx_sync import backfill_transaction_fx
from portfolio.repository import load_transactions, save_transactions
from portfolio.store import open_store


class StubMarketDataClient:
    def __init__(self, rates: dict[str, float]) -> None:
        self.requested_windows: list[tuple] = []
        self._rates = rates

    def exchange_rate_history(self, pair_symbol, start, end) -> pd.Series:
        self.requested_windows.append((start, end))
        if not self._rates:
            return pd.Series(dtype="float64")
        index = pd.to_datetime(list(self._rates))
        return pd.Series(list(self._rates.values()), index=index)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def _seed(settings, transactions):
    with open_store(settings.database_path) as connection:
        save_transactions(connection, transactions)


def _rates_by_symbol(settings) -> dict[str, float | None]:
    with open_store(settings.database_path) as connection:
        return {item.symbol: item.fx_rate for item in load_transactions(connection)}


def test_backfill_stamps_the_rate_from_the_trade_date(settings):
    _seed(settings, [transaction(symbol="MSFT", trade_day=15)])
    client = StubMarketDataClient({"2026-01-15": 31.5, "2026-01-16": 99.0})
    outcome = backfill_transaction_fx(settings, client)
    assert outcome.filled == 1
    assert outcome.is_complete
    assert _rates_by_symbol(settings)["MSFT"] == pytest.approx(31.5)


def test_backfill_never_uses_a_later_rate(settings):
    """Stamping a trade with a future rate would invent FX gains out of nothing."""
    _seed(settings, [transaction(symbol="MSFT", trade_day=15)])
    client = StubMarketDataClient({"2026-01-20": 33.0})
    outcome = backfill_transaction_fx(settings, client)
    assert outcome.filled == 0
    assert outcome.still_missing == 1
    assert _rates_by_symbol(settings)["MSFT"] is None


def test_weekend_trade_uses_the_prior_session_rate(settings):
    _seed(settings, [transaction(symbol="MSFT", trade_day=17)])
    client = StubMarketDataClient({"2026-01-16": 31.7})
    backfill_transaction_fx(settings, client)
    assert _rates_by_symbol(settings)["MSFT"] == pytest.approx(31.7)


def test_backfill_is_idempotent(settings):
    _seed(settings, [transaction(symbol="MSFT", trade_day=15)])
    client = StubMarketDataClient({"2026-01-15": 31.5})
    backfill_transaction_fx(settings, client)
    second = backfill_transaction_fx(settings, client)
    assert second.filled == 0
    assert second.is_complete


def test_a_second_run_uses_the_cache_without_refetching(settings):
    _seed(settings, [transaction(symbol="MSFT", trade_day=15)])
    client = StubMarketDataClient({"2026-01-15": 31.5})
    backfill_transaction_fx(settings, client)
    backfill_transaction_fx(settings, client)
    assert len(client.requested_windows) == 1


def test_window_covers_every_pending_trade_plus_a_lookback(settings):
    _seed(settings, [
        transaction(symbol="MSFT", trade_day=5),
        transaction(symbol="NVDA", trade_day=25),
    ])
    client = StubMarketDataClient({"2026-01-05": 31.0, "2026-01-25": 32.0})
    backfill_transaction_fx(settings, client)
    start, end = client.requested_windows[0]
    assert start < date(2026, 1, 5)
    assert end > date(2026, 1, 25)


def test_empty_database_needs_no_market_call(settings):
    outcome = backfill_transaction_fx(settings, StubMarketDataClient({}))
    assert outcome.filled == 0
    assert outcome.is_complete


def test_unavailable_rates_leave_transactions_unstamped(settings):
    _seed(settings, [transaction(symbol="MSFT", trade_day=15)])
    outcome = backfill_transaction_fx(settings, StubMarketDataClient({}))
    assert outcome.still_missing == 1
    assert not outcome.is_complete


def test_only_pending_transactions_are_refetched(settings):
    _seed(settings, [transaction(symbol="MSFT", trade_day=15)])
    client = StubMarketDataClient({"2026-01-15": 31.5})
    backfill_transaction_fx(settings, client)
    _seed(settings, [transaction(symbol="NVDA", trade_day=20)])
    client2 = StubMarketDataClient({"2026-01-20": 32.0})
    outcome = backfill_transaction_fx(settings, client2)
    assert outcome.filled == 1
    rates = _rates_by_symbol(settings)
    assert rates["MSFT"] == pytest.approx(31.5)
    assert rates["NVDA"] == pytest.approx(32.0)
