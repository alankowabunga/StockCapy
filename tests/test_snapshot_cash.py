from datetime import date

import pytest

from portfolio.cli import _cash_for_snapshot
from portfolio.performance import PortfolioPerformance
from portfolio.repository import save_position_snapshot
from portfolio.store import open_store
from test_performance import _position_performance

SNAPSHOT_DAY = date(2026, 9, 17)


@pytest.fixture
def connection(tmp_path):
    with open_store(tmp_path / "test.sqlite3") as opened:
        yield opened


def _record(connection, cash: float) -> None:
    performance = PortfolioPerformance(
        positions=[_position_performance("AAPL", 10, 100.0, 150.0, "Technology")],
        realized_gain=0.0,
    )
    save_position_snapshot(connection, performance, SNAPSHOT_DAY, cash_balance=cash)


def test_an_explicit_balance_wins_over_the_stored_one(connection):
    _record(connection, 500.0)
    assert _cash_for_snapshot(connection, SNAPSHOT_DAY, 900.0) == 900.0


def test_an_omitted_balance_keeps_the_hand_entered_one(connection):
    """The daily timer passes nothing; before this, INSERT OR REPLACE wiped the
    balance the user had typed that morning back to 0."""
    _record(connection, 500.0)
    assert _cash_for_snapshot(connection, SNAPSHOT_DAY, None) == pytest.approx(500.0)


def test_an_omitted_balance_is_zero_when_nothing_is_recorded_yet(connection):
    assert _cash_for_snapshot(connection, SNAPSHOT_DAY, None) == 0.0


def test_an_explicit_zero_is_not_mistaken_for_omitted(connection):
    _record(connection, 500.0)
    assert _cash_for_snapshot(connection, SNAPSHOT_DAY, 0.0) == 0.0


def test_yesterdays_balance_does_not_leak_into_today(connection):
    _record(connection, 500.0)
    assert _cash_for_snapshot(connection, date(2026, 9, 18), None) == 0.0
