"""A holding proven by a dividend but bought before the import window: its shares count
toward market value and weight, while anything needing a cost stays unknown."""
import pytest

from portfolio.market import Quote
from portfolio.performance import PortfolioPerformance, PositionPerformance, build_position_performance
from portfolio.holdings import Position

PRICE = 100.0


def _position(priced: float, unpriced: float, average_cost: float = 40.0) -> PositionPerformance:
    return PositionPerformance(
        symbol="VTI",
        quantity=priced,
        average_cost=average_cost,
        last_price=PRICE,
        sector="Unknown",
        unpriced_quantity=unpriced,
    )


def test_market_value_counts_shares_whose_cost_is_unknown():
    # Dropping them understated this portfolio by 30%; that was the original defect.
    assert _position(priced=1.0, unpriced=9.0).market_value == pytest.approx(1000.0)


def test_cost_basis_covers_only_the_priced_shares():
    assert _position(priced=1.0, unpriced=9.0).cost_basis == pytest.approx(40.0)


def test_gain_is_unknown_rather_than_counting_the_missing_block_as_profit():
    """market_value - cost_basis would report 960 of 'profit' on shares that simply
    have no recorded cost."""
    position = _position(priced=1.0, unpriced=9.0)
    assert position.unrealized_gain is None
    assert position.unrealized_return_percent is None
    assert not position.has_complete_cost_basis


def test_a_fully_priced_position_is_unaffected():
    position = _position(priced=10.0, unpriced=0.0)
    assert position.has_complete_cost_basis
    assert position.unrealized_gain == pytest.approx(600.0)
    assert position.unrealized_return_percent == pytest.approx(150.0)


def test_weights_use_the_full_share_count():
    incomplete = _position(priced=1.0, unpriced=9.0)
    complete = PositionPerformance("MSFT", 10.0, 50.0, PRICE, "Technology")
    portfolio = PortfolioPerformance(positions=[incomplete, complete], realized_gain=0.0)
    assert portfolio.total_market_value == pytest.approx(2000.0)
    assert portfolio.weight_of(incomplete) == pytest.approx(50.0)


def test_portfolio_totals_withhold_the_return_when_any_position_is_incomplete():
    portfolio = PortfolioPerformance(
        positions=[_position(priced=1.0, unpriced=9.0)], realized_gain=0.0
    )
    assert not portfolio.has_complete_cost_basis
    assert portfolio.total_unrealized_gain is None
    assert portfolio.total_return_percent is None
    assert portfolio.total_cost_basis == pytest.approx(40.0)  # the known part still sums


def test_attribution_is_withheld_too_because_it_splits_an_unknown_gain():
    position = PositionPerformance(
        "VTI", 1.0, 40.0, PRICE, "Unknown", average_cost_rate=31.5, rate_now=31.9,
        unpriced_quantity=9.0,
    )
    assert position.attribution is None


def test_builder_carries_the_unpriced_quantity_from_the_ledger_position():
    position = Position(symbol="VTI", quantity=1.0, total_cost=40.0, unpriced_quantity=9.0)
    built = build_position_performance(
        position, Quote("VTI", PRICE, PRICE, "USD"), sector="Unknown"
    )
    assert built.unpriced_quantity == pytest.approx(9.0)
    assert built.market_value == pytest.approx(1000.0)
