import pytest

from portfolio.valuation import (
    ValuationAssumptions,
    build_valuation_report,
    gordon_growth_value,
    graham_number,
    target_multiple_value,
)

ASSUMPTIONS = ValuationAssumptions()


def test_graham_number_uses_eps_and_book_value(dividend_payer):
    estimate = graham_number(dividend_payer)
    assert estimate.fair_value == pytest.approx((22.5 * 2.5 * 6.0) ** 0.5)
    assert estimate.is_applicable


def test_graham_number_not_applicable_when_pe_is_rich(expensive_growth_stock):
    """A high-P/E growth name is exactly where the Graham screen misleads."""
    estimate = graham_number(expensive_growth_stock)
    assert estimate.has_value
    assert not estimate.is_applicable


def test_target_multiple_uses_trailing_eps_to_match_the_multiples_basis(dividend_payer):
    """The multiple is a trailing-basis number; pairing it with forward EPS (2.8 here)
    counts the growth already priced into the multiple a second time."""
    estimate = target_multiple_value(dividend_payer, ASSUMPTIONS)
    assert estimate.fair_value == pytest.approx(2.5 * 18.0)
    assert estimate.is_applicable


def test_target_multiple_falls_back_to_forward_eps_and_says_so():
    from portfolio.market import Fundamentals

    loss_making = Fundamentals(symbol="XYZ", values={"trailingEps": -1.0, "forwardEps": 4.0})
    estimate = target_multiple_value(loss_making, ASSUMPTIONS)
    assert estimate.fair_value == pytest.approx(4.0 * 18.0)
    assert "成長算兩次" in estimate.note  # the inflation is disclosed, not hidden


def test_target_multiple_has_no_value_without_any_earnings():
    from portfolio.market import Fundamentals

    estimate = target_multiple_value(Fundamentals(symbol="XYZ", values={}), ASSUMPTIONS)
    assert estimate.fair_value is None
    assert not estimate.is_applicable


def test_gordon_growth_applies_to_a_real_dividend_payer(dividend_payer):
    estimate = gordon_growth_value(dividend_payer, ASSUMPTIONS)
    assert estimate.fair_value == pytest.approx(2.0 * 1.04 / 0.05)
    assert estimate.is_applicable


def test_gordon_growth_not_applicable_below_yield_threshold(expensive_growth_stock):
    estimate = gordon_growth_value(expensive_growth_stock, ASSUMPTIONS)
    assert not estimate.is_applicable


def test_gordon_growth_has_no_value_when_growth_exceeds_required_return(dividend_payer):
    reckless = ValuationAssumptions(required_return=0.04, dividend_growth=0.06)
    assert gordon_growth_value(dividend_payer, reckless).fair_value is None


def test_analyst_target_never_enters_the_aggregate(expensive_growth_stock):
    report = build_valuation_report("NVDA", 150.0, expensive_growth_stock, ASSUMPTIONS)
    analyst = next(e for e in report.estimates if e.model_name == "Analyst Consensus")
    assert analyst.has_value
    assert not analyst.counts_toward_aggregate
    assert analyst.fair_value not in report.aggregated_values


def test_growth_stock_aggregate_excludes_inapplicable_models(expensive_growth_stock):
    report = build_valuation_report("NVDA", 150.0, expensive_growth_stock, ASSUMPTIONS)
    assert report.aggregated_values == [pytest.approx(3.0 * 18.0)]
    assert report.median_fair_value == pytest.approx(54.0)


def test_dividend_payer_aggregates_all_three_models(dividend_payer):
    """P/E 20 keeps Graham in play, so value names get the widest model coverage."""
    report = build_valuation_report("KO", 60.0, dividend_payer, ASSUMPTIONS)
    graham = (22.5 * 2.5 * 6.0) ** 0.5
    assert sorted(report.aggregated_values) == pytest.approx(sorted([graham, 45.0, 41.6]))
    assert report.median_fair_value == pytest.approx(41.6)


def test_model_disagreement_reflects_the_spread(dividend_payer):
    report = build_valuation_report("KO", 60.0, dividend_payer, ASSUMPTIONS)
    graham = (22.5 * 2.5 * 6.0) ** 0.5
    assert report.model_disagreement_percent() == pytest.approx((45.0 - graham) / 41.6 * 100)


def test_buy_below_price_applies_margin_of_safety(dividend_payer):
    assumptions = ValuationAssumptions(margin_of_safety=0.25)
    report = build_valuation_report("KO", 60.0, dividend_payer, assumptions)
    assert report.buy_below_price(assumptions) == pytest.approx(report.median_fair_value * 0.75)


def test_upside_percent_is_relative_to_last_price(dividend_payer):
    report = build_valuation_report("KO", 41.6, dividend_payer, ASSUMPTIONS)
    assert report.upside_percent() == pytest.approx(0.0, abs=1e-9)
    cheaper = build_valuation_report("KO", 20.8, dividend_payer, ASSUMPTIONS)
    assert cheaper.upside_percent() == pytest.approx(100.0)


def test_report_with_no_usable_inputs_reports_no_midpoint():
    from portfolio.market import Fundamentals

    empty = Fundamentals(symbol="XYZ", values={})
    report = build_valuation_report("XYZ", 10.0, empty, ASSUMPTIONS)
    assert report.median_fair_value is None
    assert report.upside_percent() is None
    assert report.buy_below_price(ASSUMPTIONS) is None
