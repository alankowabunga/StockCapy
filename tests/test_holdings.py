import pytest

from conftest import transaction
from portfolio.holdings import build_ledger


def test_single_buy_sets_average_cost_including_charges():
    ledger = build_ledger([transaction(quantity=10, price=100, commission=5, fee=1)])
    position = ledger.positions["AAPL"]
    assert position.quantity == pytest.approx(10)
    assert position.average_cost == pytest.approx(100.6)


def test_two_buys_produce_weighted_average_cost():
    ledger = build_ledger([
        transaction(quantity=10, price=100, trade_day=1),
        transaction(quantity=30, price=200, trade_day=2),
    ])
    assert ledger.positions["AAPL"].average_cost == pytest.approx(175.0)


def test_partial_sell_keeps_average_cost_and_books_realized_gain():
    ledger = build_ledger([
        transaction(quantity=10, price=100, trade_day=1),
        transaction(activity="SELL", quantity=4, price=150, trade_day=2),
    ])
    position = ledger.positions["AAPL"]
    assert position.quantity == pytest.approx(6)
    assert position.average_cost == pytest.approx(100.0)
    assert position.realized_gain == pytest.approx(200.0)


def test_full_sell_closes_position_and_hides_it_from_open_positions():
    ledger = build_ledger([
        transaction(quantity=10, price=100, trade_day=1),
        transaction(activity="SELL", quantity=10, price=120, trade_day=2),
    ])
    assert ledger.open_positions() == []
    assert ledger.total_realized_gain() == pytest.approx(200.0)


def test_sell_charges_reduce_realized_gain():
    ledger = build_ledger([
        transaction(quantity=10, price=100, trade_day=1),
        transaction(activity="SELL", quantity=10, price=120, commission=7, fee=3, trade_day=2),
    ])
    assert ledger.total_realized_gain() == pytest.approx(190.0)


def test_transactions_are_applied_in_date_order_regardless_of_input_order():
    out_of_order = [
        transaction(activity="SELL", quantity=5, price=150, trade_day=9),
        transaction(quantity=10, price=100, trade_day=2),
    ]
    assert build_ledger(out_of_order).positions["AAPL"].realized_gain == pytest.approx(250.0)


def test_non_trade_activities_do_not_affect_cost_basis():
    ledger = build_ledger([
        transaction(quantity=10, price=100, trade_day=1),
        transaction(activity="DIVIDEND", quantity=0, price=0, cash_amount=21.2, trade_day=5),
        transaction(activity="WITHHOLDING", quantity=0, price=0, cash_amount=-6.36, trade_day=5),
    ])
    position = ledger.positions["AAPL"]
    assert position.quantity == pytest.approx(10)
    assert position.average_cost == pytest.approx(100.0)


def test_price_is_implied_from_cash_when_broker_omits_unit_price():
    ledger = build_ledger([
        transaction(quantity=10, price=0, cash_amount=-1500.0),
    ])
    assert ledger.positions["AAPL"].average_cost == pytest.approx(150.0)


def test_rows_without_a_symbol_are_ignored():
    ledger = build_ledger([transaction(symbol="", quantity=10, price=100)])
    assert ledger.open_positions() == []


def test_separate_symbols_are_tracked_independently():
    ledger = build_ledger([
        transaction(symbol="AAPL", quantity=10, price=100),
        transaction(symbol="MSFT", quantity=5, price=400),
    ])
    assert [p.symbol for p in ledger.open_positions()] == ["AAPL", "MSFT"]


def test_purchase_records_home_cost_at_the_trade_date_rate():
    ledger = build_ledger([transaction(quantity=10, price=100, fx_rate=32.0)])
    position = ledger.positions["AAPL"]
    assert position.total_cost == pytest.approx(1000.0)
    assert position.total_cost_home == pytest.approx(32000.0)
    assert position.average_cost_rate == pytest.approx(32.0)


def test_two_purchases_blend_the_rates_by_amount_invested():
    """A larger later purchase must pull the average rate toward its own rate."""
    ledger = build_ledger([
        transaction(quantity=10, price=100, fx_rate=32.0, trade_day=1),
        transaction(quantity=30, price=100, fx_rate=31.0, trade_day=2),
    ])
    position = ledger.positions["AAPL"]
    assert position.total_cost == pytest.approx(4000.0)
    assert position.total_cost_home == pytest.approx(10 * 100 * 32.0 + 30 * 100 * 31.0)
    assert position.average_cost_rate == pytest.approx(31.25)


def test_partial_sale_keeps_the_average_rate_paid():
    """Selling shares does not change the rate you paid on the shares you keep."""
    ledger = build_ledger([
        transaction(quantity=10, price=100, fx_rate=32.0, trade_day=1),
        transaction(activity="SELL", quantity=4, price=150, fx_rate=31.0, trade_day=2),
    ])
    position = ledger.positions["AAPL"]
    assert position.total_cost == pytest.approx(600.0)
    assert position.total_cost_home == pytest.approx(600.0 * 32.0)
    assert position.average_cost_rate == pytest.approx(32.0)


def test_transactions_without_rates_leave_home_cost_unset():
    """Pre-FX imports must report None rather than a fabricated rate."""
    ledger = build_ledger([transaction(quantity=10, price=100)])
    position = ledger.positions["AAPL"]
    assert position.total_cost_home == pytest.approx(0.0)
    assert position.average_cost_rate is None


def test_a_position_mixing_rated_and_unrated_trades_reports_a_partial_rate():
    """Known limitation: the average only covers the trades that carry a rate."""
    ledger = build_ledger([
        transaction(quantity=10, price=100, fx_rate=32.0, trade_day=1),
        transaction(quantity=10, price=100, trade_day=2),
    ])
    position = ledger.positions["AAPL"]
    assert position.total_cost == pytest.approx(2000.0)
    assert position.total_cost_home == pytest.approx(32000.0)
    assert position.average_cost_rate == pytest.approx(16.0)
