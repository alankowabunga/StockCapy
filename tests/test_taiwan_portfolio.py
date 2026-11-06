import pytest

from portfolio.dividends import forecast_dividends, taiwan_deduction_rate
from portfolio.holdings import Position, PositionLedger
from portfolio.market import Quote
from portfolio.performance import (
    ExchangeRateUnavailableError,
    PortfolioPerformance,
    build_position_performance,
)

USD_TO_TWD = 30.0


def _quote(symbol: str, price: float) -> Quote:
    return Quote(symbol=symbol, last_price=price, previous_close=price, currency="USD")


def _us(symbol="AAPL", quantity=10, avg=100.0, price=150.0, rate_now=None):
    position = Position(symbol=symbol, quantity=quantity, total_cost=quantity * avg)
    return build_position_performance(position, _quote(symbol, price), "Tech", rate_now)


def _tw(symbol="2330.TW", quantity=1000, avg=600.0, price=900.0, home="TWD"):
    position = Position(symbol=symbol, quantity=quantity, total_cost=quantity * avg)
    return build_position_performance(
        position, _quote(symbol, price), "Semis", USD_TO_TWD, USD_TO_TWD, home
    )


def test_a_taiwan_holding_is_converted_to_usd_for_totals():
    holding = _tw()
    assert holding.market_value == pytest.approx(900_000)  # TWD, its own currency
    assert holding.market_value_usd == pytest.approx(30_000)
    assert holding.cost_basis_usd == pytest.approx(20_000)


def test_us_only_portfolio_totals_are_unchanged():
    portfolio = PortfolioPerformance(positions=[_us()], realized_gain=0.0)
    assert portfolio.total_market_value == pytest.approx(1500.0)
    assert portfolio.has_taiwan_positions is False
    assert portfolio.total_market_value_twd is None


def test_mixed_portfolio_totals_in_usd_and_twd():
    portfolio = PortfolioPerformance(positions=[_us(), _tw()], realized_gain=0.0)
    assert portfolio.total_market_value == pytest.approx(1500 + 30_000)
    assert portfolio.total_market_value_twd == pytest.approx((1500 + 30_000) * USD_TO_TWD)
    us, tw = portfolio.market_subtotal(taiwan=False), portfolio.market_subtotal(taiwan=True)
    assert us == pytest.approx((1500.0, 1000.0))
    assert tw == pytest.approx((900_000.0, 600_000.0))


def test_weights_use_usd_so_a_big_twd_number_does_not_swamp_the_portfolio():
    portfolio = PortfolioPerformance(positions=[_us(), _tw()], realized_gain=0.0)
    weights = [portfolio.weight_of(p) for p in portfolio.positions]
    assert sum(weights) == pytest.approx(100.0)
    assert weights[1] == pytest.approx(30_000 / 31_500 * 100)


def test_taiwan_gain_has_no_currency_part_when_home_is_twd():
    holding = _tw()
    attribution = holding.attribution
    assert attribution is not None
    assert attribution.currency_gain_home == pytest.approx(0.0)
    assert attribution.stock_gain_home == pytest.approx(300_000)
    assert attribution.total_gain_home == pytest.approx(holding.unrealized_gain)
    assert attribution.cost_home == pytest.approx(600_000)


def test_taiwan_has_no_attribution_when_home_is_not_twd():
    assert _tw(home="JPY").attribution is None


def test_taiwan_needs_the_exchange_rate():
    position = Position(symbol="2330.TW", quantity=1, total_cost=600.0)
    with pytest.raises(ExchangeRateUnavailableError):
        build_position_performance(position, _quote("2330.TW", 900.0), "Semis", None, None, "TWD")


def test_realized_gain_is_split_by_market():
    ledger = PositionLedger()
    ledger.positions["AAPL"] = Position(symbol="AAPL", realized_gain=100.0)
    ledger.positions["2330.TW"] = Position(symbol="2330.TW", realized_gain=60_000.0)
    assert ledger.realized_gain_by_market() == (100.0, 60_000.0)


@pytest.mark.parametrize(
    ("payment", "rate"),
    [(19_999.0, 0.0), (20_000.0, 0.0211), (100_000.0, 0.0211)],
)
def test_taiwan_deduction_applies_from_twenty_thousand(payment, rate):
    assert taiwan_deduction_rate(payment) == rate


class _DividendClient:
    def __init__(self, rates):
        self._rates = rates

    def fundamentals(self, symbol):
        rate = self._rates[symbol]

        class _F:
            def number(self, name):
                return rate

        return _F()


def test_forecast_uses_taiwan_rule_for_tw_and_nra_rate_for_us():
    positions = [
        Position(symbol="AAPL", quantity=100),
        Position(symbol="2330.TW", quantity=2000),  # 2000 * 10 = NT$20,000
        Position(symbol="0050.TW", quantity=100),  # 100 * 10 = NT$1,000
    ]
    client = _DividendClient({"AAPL": 1.0, "2330.TW": 10.0, "0050.TW": 10.0})
    by_symbol = {f.symbol: f for f in forecast_dividends(client, positions, 0.30)}
    assert by_symbol["AAPL"].withholding_rate == 0.30
    assert by_symbol["2330.TW"].withholding_rate == 0.0211
    assert by_symbol["0050.TW"].withholding_rate == 0.0
    assert by_symbol["0050.TW"].net_annual_income == pytest.approx(1000.0)


def test_api_summary_adds_market_subtotals_only_with_taiwan_holdings():
    from portfolio.web.serializers import serialize_portfolio

    us_only = serialize_portfolio(PortfolioPerformance(positions=[_us()], realized_gain=0.0))
    assert us_only["summary"]["market_subtotals"] is None

    mixed = serialize_portfolio(PortfolioPerformance(positions=[_us(), _tw()], realized_gain=0.0))
    subtotals = mixed["summary"]["market_subtotals"]
    assert subtotals["usd_to_twd"] == pytest.approx(USD_TO_TWD)
    assert subtotals["market_value_twd"] == pytest.approx(31_500 * USD_TO_TWD)
    assert subtotals["tw"]["market_value"] == pytest.approx(900_000)
    assert subtotals["us"]["market_value"] == pytest.approx(1_500)
    # The snapshot unit stays USD (D31).
    assert mixed["summary"]["market_value"] == pytest.approx(31_500)


def test_report_summary_leads_with_twd_when_taiwan_is_held():
    import io

    from rich.console import Console

    from portfolio.report import render_portfolio_summary

    def render(positions):
        buffer = io.StringIO()
        render_portfolio_summary(
            Console(file=buffer, width=120), PortfolioPerformance(positions, 0.0)
        )
        return buffer.getvalue()

    mixed = render([_us(), _tw()])
    assert "NT$945,000.00" in mixed and "美股小計" in mixed and "台股小計" in mixed
    us_only = render([_us()])
    assert "NT$" not in us_only and "$1,500.00" in us_only
