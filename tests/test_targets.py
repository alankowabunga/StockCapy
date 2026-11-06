import pytest

from portfolio.holdings import Position
from portfolio.market import Fundamentals, PricePosition, Quote
from portfolio.targets import build_entry_target, build_entry_targets
from portfolio.valuation import ValuationAssumptions

ASSUMPTIONS = ValuationAssumptions(target_price_earnings=18.0, margin_of_safety=0.20)


class StubMarketDataClient:
    def __init__(self, price: float, fundamentals: Fundamentals, position: PricePosition) -> None:
        self._price = price
        self._fundamentals = fundamentals
        self._position = position

    def quote(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, last_price=self._price, previous_close=self._price, currency="USD")

    def fundamentals(self, symbol: str) -> Fundamentals:
        return self._fundamentals

    def price_position(self, symbol: str) -> PricePosition:
        return self._position


def _price_position(quote_type: str, price: float = 100.0) -> PricePosition:
    return PricePosition(
        symbol="X", last_price=price, week52_low=80.0, week52_high=120.0,
        two_hundred_day_average=95.0, quote_type=quote_type,
    )


def test_stock_with_applicable_model_uses_the_valuation_basis(dividend_payer):
    client = StubMarketDataClient(60.0, dividend_payer, _price_position("EQUITY", 60.0))
    target = build_entry_target(client, "KO", ASSUMPTIONS, average_cost=50.0)
    assert target.basis == "估值模型"
    assert target.reference_price == pytest.approx(target.valuation.median_fair_value)
    assert target.buy_below == pytest.approx(target.reference_price * 0.8)


def test_etf_without_fundamentals_falls_back_to_the_moving_average():
    """An ETF has no EPS, so the only honest anchor is its own trend line."""
    empty = Fundamentals(symbol="VTI", values={"quoteType": "ETF"})
    client = StubMarketDataClient(100.0, empty, _price_position("ETF"))
    target = build_entry_target(client, "VTI", ASSUMPTIONS)
    assert target.model_count == 0
    assert target.basis == "200日均線"
    assert target.reference_price == pytest.approx(95.0)
    assert target.buy_below == pytest.approx(76.0)


def test_failed_fundamentals_lookup_is_reported_as_missing_data():
    """A rate-limited lookup must not look like a company that cannot be valued."""
    failed = Fundamentals(symbol="XYZ", values={})
    client = StubMarketDataClient(100.0, failed, _price_position("EQUITY"))
    target = build_entry_target(client, "XYZ", ASSUMPTIONS)
    assert target.basis == "資料不足"
    assert target.buy_below is None
    assert not target.is_at_or_below_target


def test_stock_fetched_successfully_but_unvaluable_has_no_basis():
    """Fundamentals arrived - the company simply has no EPS or book value."""
    unvaluable = Fundamentals(
        symbol="XYZ",
        values={"sector": "Energy", "currency": "USD", "quoteType": "EQUITY",
                "trailingEps": None, "bookValue": None, "dividendRate": None},
    )
    client = StubMarketDataClient(100.0, unvaluable, _price_position("EQUITY"))
    target = build_entry_target(client, "XYZ", ASSUMPTIONS)
    assert target.basis == "無"
    assert target.buy_below is None
    assert target.gap_to_buy_below_percent is None


def test_fund_with_no_fundamentals_still_prefers_the_moving_average():
    """An ETF legitimately reports no EPS, so 200MA wins over the missing-data label."""
    fund = Fundamentals(symbol="VTI", values={"quoteType": "ETF", "sector": None})
    client = StubMarketDataClient(100.0, fund, _price_position("ETF"))
    assert build_entry_target(client, "VTI", ASSUMPTIONS).basis == "200日均線"


def test_gap_and_trigger_reflect_the_current_price(dividend_payer):
    client = StubMarketDataClient(20.0, dividend_payer, _price_position("EQUITY", 20.0))
    target = build_entry_target(client, "KO", ASSUMPTIONS)
    assert target.is_at_or_below_target
    assert target.gap_to_buy_below_percent < 0


def test_average_cost_comparison_is_reported(dividend_payer):
    client = StubMarketDataClient(60.0, dividend_payer, _price_position("EQUITY", 60.0))
    target = build_entry_target(client, "KO", ASSUMPTIONS, average_cost=50.0)
    assert target.discount_to_average_cost_percent == pytest.approx(20.0)


def test_average_cost_is_optional_for_a_watchlist_only_symbol(dividend_payer):
    client = StubMarketDataClient(60.0, dividend_payer, _price_position("EQUITY", 60.0))
    target = build_entry_target(client, "KO", ASSUMPTIONS)
    assert target.discount_to_average_cost_percent is None


def test_targets_pull_average_cost_from_positions(dividend_payer):
    client = StubMarketDataClient(60.0, dividend_payer, _price_position("EQUITY", 60.0))
    positions = [Position(symbol="KO", quantity=10, total_cost=500.0)]
    targets = build_entry_targets(client, {"KO": ASSUMPTIONS}, positions)
    assert targets[0].average_cost == pytest.approx(50.0)


def test_targets_are_sorted_with_the_closest_to_entry_first(dividend_payer):
    client = StubMarketDataClient(20.0, dividend_payer, _price_position("EQUITY", 20.0))
    targets = build_entry_targets(client, {"KO": ASSUMPTIONS, "KO2": ASSUMPTIONS})
    gaps = [t.gap_to_buy_below_percent for t in targets]
    assert gaps == sorted(gaps)


def test_each_symbol_can_carry_a_different_target_pe(dividend_payer):
    """One symbol's assumption must not leak into another's valuation."""
    client = StubMarketDataClient(60.0, dividend_payer, _price_position("EQUITY", 60.0))
    aggressive = ValuationAssumptions(target_price_earnings=36.0, margin_of_safety=0.20)
    targets = {t.symbol: t for t in build_entry_targets(
        client, {"A": ASSUMPTIONS, "B": aggressive})}

    def target_pe_estimate(entry):
        return next(e for e in entry.valuation.estimates if e.model_name == "Target P/E")

    assert target_pe_estimate(targets["A"]).fair_value == pytest.approx(2.5 * 18.0)
    assert target_pe_estimate(targets["B"]).fair_value == pytest.approx(2.5 * 36.0)


def test_median_resists_a_single_shifted_assumption(dividend_payer):
    """With three applicable models the median absorbs one outlier - that is intended."""
    client = StubMarketDataClient(60.0, dividend_payer, _price_position("EQUITY", 60.0))
    aggressive = ValuationAssumptions(target_price_earnings=36.0, margin_of_safety=0.20)
    baseline = build_entry_target(client, "KO", ASSUMPTIONS)
    shifted = build_entry_target(client, "KO", aggressive)
    assert baseline.reference_price == pytest.approx(shifted.reference_price)


def test_margin_of_safety_still_moves_the_entry_price(dividend_payer):
    client = StubMarketDataClient(60.0, dividend_payer, _price_position("EQUITY", 60.0))
    tight = build_entry_target(client, "KO", ValuationAssumptions(margin_of_safety=0.10))
    wide = build_entry_target(client, "KO", ValuationAssumptions(margin_of_safety=0.30))
    assert tight.buy_below > wide.buy_below


def test_price_position_percentile_and_trend_premium():
    position = _price_position("EQUITY", 100.0)
    assert position.range_percentile == pytest.approx(50.0)
    assert position.premium_to_two_hundred_day_percent == pytest.approx(5.263158)
    assert not position.is_fund


def test_price_position_handles_a_degenerate_range():
    flat = PricePosition("X", 100.0, 0.0, 0.0, 0.0, "EQUITY")
    assert flat.range_percentile is None
    assert flat.premium_to_two_hundred_day_percent is None
