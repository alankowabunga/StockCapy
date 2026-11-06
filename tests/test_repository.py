import pytest

from conftest import transaction
from portfolio.performance import PortfolioPerformance
from portfolio.repository import (
    load_account_value_history,
    load_cash_balance_on,
    load_transactions,
    save_position_snapshot,
    save_transactions,
)
from portfolio.store import open_store
from test_performance import _position_performance


@pytest.fixture
def database_path(tmp_path):
    return tmp_path / "test.sqlite3"


def test_saved_transactions_round_trip(database_path):
    original = [transaction(symbol="AAPL"), transaction(symbol="KO", activity="DIVIDEND")]
    with open_store(database_path) as connection:
        assert save_transactions(connection, original) == 2
    with open_store(database_path) as connection:
        loaded = load_transactions(connection)
    assert [t.symbol for t in loaded] == ["AAPL", "KO"]
    assert loaded[0].trade_date == original[0].trade_date


def test_reimporting_the_same_csv_inserts_nothing(database_path):
    """Overlapping broker exports are the norm; a re-import must not double-count."""
    rows = [transaction(symbol="AAPL"), transaction(symbol="KO")]
    with open_store(database_path) as connection:
        assert save_transactions(connection, rows) == 2
        assert save_transactions(connection, rows) == 0
        assert len(load_transactions(connection)) == 2


def test_a_genuinely_new_row_is_still_inserted_on_reimport(database_path):
    with open_store(database_path) as connection:
        save_transactions(connection, [transaction(symbol="AAPL", trade_day=1)])
        inserted = save_transactions(
            connection,
            [transaction(symbol="AAPL", trade_day=1), transaction(symbol="AAPL", trade_day=2)],
        )
    assert inserted == 1


def test_snapshot_writes_positions_and_account_total(database_path):
    from datetime import date

    performance = PortfolioPerformance(
        positions=[_position_performance("AAPL", 10, 100.0, 150.0, "Technology")],
        realized_gain=0.0,
    )
    with open_store(database_path) as connection:
        save_position_snapshot(connection, performance, date(2026, 9, 16), cash_balance=500.0)
        history = load_account_value_history(connection)
    assert history == [("2026-09-16", pytest.approx(2000.0))]


def test_snapshot_for_the_same_day_overwrites_instead_of_duplicating(database_path):
    from datetime import date

    performance = PortfolioPerformance(
        positions=[_position_performance("AAPL", 10, 100.0, 150.0, "Technology")],
        realized_gain=0.0,
    )
    with open_store(database_path) as connection:
        save_position_snapshot(connection, performance, date(2026, 9, 16), cash_balance=0.0)
        save_position_snapshot(connection, performance, date(2026, 9, 16), cash_balance=100.0)
        history = load_account_value_history(connection)
    assert len(history) == 1
    assert history[0][1] == pytest.approx(1600.0)



def test_cash_balance_reads_back_for_a_recorded_day(database_path):
    from datetime import date

    performance = PortfolioPerformance(
        positions=[_position_performance("AAPL", 10, 100.0, 150.0, "Technology")],
        realized_gain=0.0,
    )
    with open_store(database_path) as connection:
        save_position_snapshot(connection, performance, date(2026, 9, 16), cash_balance=500.0)
        assert load_cash_balance_on(connection, date(2026, 9, 16)) == pytest.approx(500.0)


def test_cash_balance_is_none_for_a_day_with_no_snapshot(database_path):
    # None must stay distinguishable from 0.0, or the daily scheduler cannot tell
    # "nothing recorded yet" from "the user really has no cash".
    from datetime import date

    with open_store(database_path) as connection:
        assert load_cash_balance_on(connection, date(2026, 9, 16)) is None


def test_a_recorded_zero_cash_balance_reads_back_as_zero_not_none(database_path):
    from datetime import date

    performance = PortfolioPerformance(
        positions=[_position_performance("AAPL", 10, 100.0, 150.0, "Technology")],
        realized_gain=0.0,
    )
    with open_store(database_path) as connection:
        save_position_snapshot(connection, performance, date(2026, 9, 16), cash_balance=0.0)
        assert load_cash_balance_on(connection, date(2026, 9, 16)) == 0.0


def test_empty_database_returns_no_transactions(database_path):
    with open_store(database_path) as connection:
        assert load_transactions(connection) == []
        assert load_account_value_history(connection) == []


def test_manual_position_round_trips_as_a_position(database_path):
    from portfolio.holdings import ManualPosition
    from portfolio.repository import load_manual_positions, save_manual_position

    with open_store(database_path) as connection:
        save_manual_position(connection, ManualPosition("vti", 10.05, 300.00, "pre-export holding"))
        positions = load_manual_positions(connection)
    assert positions[0].symbol == "VTI"
    assert positions[0].average_cost == pytest.approx(300.00)
    assert positions[0].total_cost == pytest.approx(10.05 * 300.00)


def test_saving_the_same_manual_symbol_twice_updates_it(database_path):
    from portfolio.holdings import ManualPosition
    from portfolio.repository import load_manual_positions, save_manual_position

    with open_store(database_path) as connection:
        save_manual_position(connection, ManualPosition("VTI", 10, 300.0))
        save_manual_position(connection, ManualPosition("VTI", 20, 350.0))
        positions = load_manual_positions(connection)
    assert len(positions) == 1
    assert positions[0].quantity == pytest.approx(20)


def test_deleting_a_manual_position_reports_whether_it_existed(database_path):
    from portfolio.holdings import ManualPosition
    from portfolio.repository import delete_manual_position, save_manual_position

    with open_store(database_path) as connection:
        save_manual_position(connection, ManualPosition("VTI", 10, 300.0))
        assert delete_manual_position(connection, "VTI") is True
        assert delete_manual_position(connection, "VTI") is False


def test_alert_rule_round_trips(database_path):
    from portfolio.repository import load_alert_rules, save_alert_rule

    with open_store(database_path) as connection:
        save_alert_rule(connection, "MSFT", 400.0, 600.0)
        rules = load_alert_rules(connection)
    assert rules[0].symbol == "MSFT"
    assert rules[0].buy_below == pytest.approx(400.0)
    assert rules[0].sell_above == pytest.approx(600.0)


def test_updating_only_the_buy_target_preserves_the_sell_target(database_path):
    """`watch add --buy-below` must not silently wipe an existing sell target."""
    from portfolio.repository import load_alert_rules, save_alert_rule

    with open_store(database_path) as connection:
        save_alert_rule(connection, "MSFT", 400.0, 600.0)
        save_alert_rule(connection, "MSFT", 380.0, None)
        rules = load_alert_rules(connection)
    assert rules[0].buy_below == pytest.approx(380.0)
    assert rules[0].sell_above == pytest.approx(600.0)


def test_marking_an_alert_fired_records_the_date(database_path):
    from datetime import date

    from portfolio.repository import load_alert_rules, mark_alert_fired, save_alert_rule

    with open_store(database_path) as connection:
        save_alert_rule(connection, "MSFT", 400.0, None)
        mark_alert_fired(connection, "MSFT", date(2026, 9, 16))
        rules = load_alert_rules(connection)
    assert rules[0].already_fired_on(date(2026, 9, 16))


def test_saving_a_new_buy_target_clears_the_fired_flag(database_path):
    """Changing the target must re-arm the alert, or a new target would never fire."""
    from datetime import date

    from portfolio.repository import load_alert_rules, mark_alert_fired, save_alert_rule

    with open_store(database_path) as connection:
        save_alert_rule(connection, "MSFT", 400.0, None)
        mark_alert_fired(connection, "MSFT", date(2026, 9, 16))
        save_alert_rule(connection, "MSFT", 450.0, None)
        rules = load_alert_rules(connection)
    assert not rules[0].already_fired_on(date(2026, 9, 16))


def test_migration_adds_withheld_amount_to_a_legacy_database(database_path):
    """A database created before the withholding column must keep working."""
    import sqlite3

    from portfolio.store import apply_migrations, create_schema

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE transactions (id INTEGER PRIMARY KEY, trade_date TEXT NOT NULL,"
        " settled_date TEXT, symbol TEXT NOT NULL, activity TEXT NOT NULL, quantity REAL,"
        " price REAL, cash_amount REAL, commission REAL, fee REAL, description TEXT,"
        " row_fingerprint TEXT NOT NULL UNIQUE)"
    )
    apply_migrations(connection)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(transactions)")}
    connection.close()
    assert "withheld_amount" in columns
