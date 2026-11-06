import pandas as pd
import pytest

from portfolio.holdings import Position
from portfolio.market import Quote
from portfolio.performance import (
    PortfolioPerformance,
    build_position_performance,
    max_drawdown_percent,
    sharpe_ratio,
)


def _position_performance(symbol: str, quantity: float, avg_cost: float, price: float, sector: str):
    position = Position(symbol=symbol, quantity=quantity, total_cost=quantity * avg_cost)
    quote = Quote(symbol=symbol, last_price=price, previous_close=price, currency="USD")
    return build_position_performance(position, quote, sector)


@pytest.fixture
def two_position_portfolio() -> PortfolioPerformance:
    return PortfolioPerformance(
        positions=[
            _position_performance("AAPL", 10, 100.0, 150.0, "Technology"),
            _position_performance("KO", 20, 60.0, 50.0, "Consumer Defensive"),
        ],
        realized_gain=25.0,
    )


def test_position_unrealized_gain_and_return(two_position_portfolio):
    apple = two_position_portfolio.positions[0]
    assert apple.market_value == pytest.approx(1500.0)
    assert apple.cost_basis == pytest.approx(1000.0)
    assert apple.unrealized_gain == pytest.approx(500.0)
    assert apple.unrealized_return_percent == pytest.approx(50.0)


def test_losing_position_reports_negative_return(two_position_portfolio):
    coke = two_position_portfolio.positions[1]
    assert coke.unrealized_gain == pytest.approx(-200.0)
    assert coke.unrealized_return_percent == pytest.approx(-16.666667)


def test_portfolio_totals_net_the_positions(two_position_portfolio):
    assert two_position_portfolio.total_market_value == pytest.approx(2500.0)
    assert two_position_portfolio.total_cost_basis == pytest.approx(2200.0)
    assert two_position_portfolio.total_unrealized_gain == pytest.approx(300.0)
    assert two_position_portfolio.total_return_percent == pytest.approx(300 / 2200 * 100)


def test_weights_sum_to_one_hundred_percent(two_position_portfolio):
    weights = [two_position_portfolio.weight_of(p) for p in two_position_portfolio.positions]
    assert sum(weights) == pytest.approx(100.0)
    assert weights[0] == pytest.approx(60.0)


def test_sector_exposure_groups_and_sorts_by_weight(two_position_portfolio):
    exposure = two_position_portfolio.exposure_by_sector()
    assert list(exposure) == ["Technology", "Consumer Defensive"]
    assert exposure["Technology"] == pytest.approx(60.0)


def test_sector_exposure_merges_positions_in_the_same_sector():
    portfolio = PortfolioPerformance(
        positions=[
            _position_performance("AAPL", 10, 100.0, 100.0, "Technology"),
            _position_performance("MSFT", 10, 100.0, 100.0, "Technology"),
        ],
        realized_gain=0.0,
    )
    assert portfolio.exposure_by_sector() == {"Technology": pytest.approx(100.0)}


def test_missing_sector_is_labeled_unknown():
    portfolio = PortfolioPerformance(
        positions=[_position_performance("XYZ", 1, 10.0, 10.0, "")], realized_gain=0.0
    )
    assert "Unknown" in portfolio.exposure_by_sector()


def test_empty_portfolio_does_not_divide_by_zero():
    empty = PortfolioPerformance(positions=[], realized_gain=0.0)
    assert empty.total_return_percent == 0.0
    assert empty.exposure_by_sector() == {}


def test_max_drawdown_finds_the_worst_peak_to_trough():
    returns = pd.Series([0.10, -0.20, -0.10, 0.05])
    assert max_drawdown_percent(returns) == pytest.approx(-28.0, abs=0.2)


def test_max_drawdown_is_zero_when_never_losing():
    assert max_drawdown_percent(pd.Series([0.01, 0.02, 0.03])) == pytest.approx(0.0)


def test_max_drawdown_handles_empty_series():
    assert max_drawdown_percent(pd.Series(dtype="float64")) == 0.0


def test_sharpe_ratio_is_zero_for_constant_returns():
    assert sharpe_ratio(pd.Series([0.001] * 10)) == 0.0


def test_sharpe_ratio_is_positive_for_a_rising_noisy_series():
    returns = pd.Series([0.01, -0.002, 0.008, 0.004, 0.012, -0.001])
    assert sharpe_ratio(returns) > 0


def test_sharpe_ratio_survives_floating_point_noise_in_a_flat_series():
    """A flat series has std ~1e-19, not 0 — the naive guard produced ~1e16."""
    assert sharpe_ratio(pd.Series([0.001] * 50)) == 0.0
