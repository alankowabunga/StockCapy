from datetime import date, timedelta

import pytest

from portfolio.assumptions import (
    GLOBAL_DEFAULT_KEY,
    SOURCE_DEFAULT,
    SOURCE_HISTORICAL,
    SOURCE_MANUAL,
    AssumptionOverride,
    delete_override,
    load_all_overrides,
    load_cached_historical,
    load_global_default,
    load_override,
    resolve_assumptions,
    save_cached_historical,
    save_override,
)
from portfolio.historical_pe import HistoricalPriceEarnings
from portfolio.store import open_store
from portfolio.valuation import ValuationAssumptions

TODAY = date(2026, 9, 16)


class StubMarketDataClient:
    """resolve_assumptions only ever needs the historical P/E inputs."""

    def __init__(self, annual_eps: dict | None = None) -> None:
        self._annual_eps = annual_eps or {}

    def close_prices(self, symbol, start, end):
        import pandas as pd

        return pd.Series(dtype="float64")

    def annual_diluted_eps(self, symbol):
        return self._annual_eps


@pytest.fixture
def database_path(tmp_path):
    return tmp_path / "assumptions.sqlite3"


def test_empty_override_is_detected():
    assert AssumptionOverride("MSFT").is_empty()
    assert not AssumptionOverride("MSFT", target_pe=20.0).is_empty()


def test_override_applies_only_the_fields_that_are_set():
    base = ValuationAssumptions(target_price_earnings=18.0, margin_of_safety=0.20)
    applied = AssumptionOverride("MSFT", target_pe=30.0).apply_to(base)
    assert applied.target_price_earnings == pytest.approx(30.0)
    assert applied.margin_of_safety == pytest.approx(0.20)


def test_empty_override_leaves_the_base_untouched():
    base = ValuationAssumptions()
    assert AssumptionOverride("MSFT").apply_to(base) is base


def test_override_round_trips(database_path):
    with open_store(database_path) as connection:
        save_override(connection, AssumptionOverride("msft", target_pe=28.0))
        loaded = load_override(connection, "MSFT")
    assert loaded.symbol == "MSFT"
    assert loaded.target_pe == pytest.approx(28.0)


def test_partial_update_preserves_previously_saved_fields(database_path):
    """Setting only the margin must not wipe a stored target P/E."""
    with open_store(database_path) as connection:
        save_override(connection, AssumptionOverride("MSFT", target_pe=28.0))
        save_override(connection, AssumptionOverride("MSFT", margin_of_safety=0.15))
        loaded = load_override(connection, "MSFT")
    assert loaded.target_pe == pytest.approx(28.0)
    assert loaded.margin_of_safety == pytest.approx(0.15)


def test_global_default_row_is_excluded_from_the_symbol_list(database_path):
    with open_store(database_path) as connection:
        save_override(connection, AssumptionOverride(GLOBAL_DEFAULT_KEY, target_pe=20.0))
        save_override(connection, AssumptionOverride("MSFT", target_pe=28.0))
        symbols = [o.symbol for o in load_all_overrides(connection)]
    assert symbols == ["MSFT"]


def test_global_default_feeds_the_base_assumptions(database_path):
    with open_store(database_path) as connection:
        save_override(connection, AssumptionOverride(GLOBAL_DEFAULT_KEY, target_pe=22.0))
        default = load_global_default(connection)
    assert default.target_price_earnings == pytest.approx(22.0)


def test_built_in_default_is_used_when_nothing_is_stored(database_path):
    with open_store(database_path) as connection:
        assert load_global_default(connection).target_price_earnings == pytest.approx(18.0)


def test_delete_override_reports_whether_it_existed(database_path):
    with open_store(database_path) as connection:
        save_override(connection, AssumptionOverride("MSFT", target_pe=28.0))
        assert delete_override(connection, "MSFT") is True
        assert delete_override(connection, "MSFT") is False


def test_fresh_cache_is_returned(database_path):
    historical = HistoricalPriceEarnings("MSFT", 35.3, 32.5, 37.8, 805)
    with open_store(database_path) as connection:
        save_cached_historical(connection, historical, TODAY)
        cached = load_cached_historical(connection, "MSFT", TODAY)
    assert cached.median_pe == pytest.approx(35.3)


def test_stale_cache_is_ignored(database_path):
    historical = HistoricalPriceEarnings("MSFT", 35.3, 32.5, 37.8, 805)
    with open_store(database_path) as connection:
        save_cached_historical(connection, historical, TODAY - timedelta(days=90))
        assert load_cached_historical(connection, "MSFT", TODAY) is None


def test_missing_cache_returns_none(database_path):
    with open_store(database_path) as connection:
        assert load_cached_historical(connection, "NOPE", TODAY) is None


def test_stable_historical_pe_becomes_the_target(database_path):
    stable = HistoricalPriceEarnings("MSFT", 35.3, 32.5, 37.8, 805)
    with open_store(database_path) as connection:
        save_cached_historical(connection, stable, date.today())
        resolved = resolve_assumptions(connection, StubMarketDataClient(), "MSFT")
    assert resolved.source == SOURCE_HISTORICAL
    assert resolved.assumptions.target_price_earnings == pytest.approx(35.3)
    assert resolved.historical_median_pe == pytest.approx(35.3)


def test_unstable_historical_pe_is_refused_in_favour_of_the_default(database_path):
    """A 46-134 spread must not masquerade as a precise target."""
    unstable = HistoricalPriceEarnings("NVDA", 73.9, 46.0, 134.3, 909)
    with open_store(database_path) as connection:
        save_cached_historical(connection, unstable, date.today())
        resolved = resolve_assumptions(connection, StubMarketDataClient(), "NVDA")
    assert resolved.source == SOURCE_DEFAULT
    assert resolved.assumptions.target_price_earnings == pytest.approx(18.0)
    assert resolved.historical_median_pe == pytest.approx(73.9)


def test_manual_override_beats_the_historical_value(database_path):
    stable = HistoricalPriceEarnings("MSFT", 35.3, 32.5, 37.8, 805)
    with open_store(database_path) as connection:
        save_cached_historical(connection, stable, date.today())
        save_override(connection, AssumptionOverride("MSFT", target_pe=30.0))
        resolved = resolve_assumptions(connection, StubMarketDataClient(), "MSFT")
    assert resolved.source == SOURCE_MANUAL
    assert resolved.assumptions.target_price_earnings == pytest.approx(30.0)


def test_manual_margin_override_keeps_the_historical_target_pe(database_path):
    stable = HistoricalPriceEarnings("MSFT", 35.3, 32.5, 37.8, 805)
    with open_store(database_path) as connection:
        save_cached_historical(connection, stable, date.today())
        save_override(connection, AssumptionOverride("MSFT", margin_of_safety=0.15))
        resolved = resolve_assumptions(connection, StubMarketDataClient(), "MSFT")
    assert resolved.assumptions.target_price_earnings == pytest.approx(35.3)
    assert resolved.assumptions.margin_of_safety == pytest.approx(0.15)


def test_fund_without_eps_falls_back_to_the_global_default(database_path):
    with open_store(database_path) as connection:
        save_override(connection, AssumptionOverride(GLOBAL_DEFAULT_KEY, target_pe=21.0))
        resolved = resolve_assumptions(connection, StubMarketDataClient(), "VTI")
    assert resolved.source == SOURCE_DEFAULT
    assert resolved.assumptions.target_price_earnings == pytest.approx(21.0)
    assert resolved.historical_median_pe is None
