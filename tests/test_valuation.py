import pytest

from portfolio.market import Fundamentals
from portfolio.valuation import (
    DCF_YEARS,
    ValuationAssumptions,
    build_valuation_report,
    dcf_value,
    gordon_growth_value,
    graham_number,
    implied_earnings_growth,
    peg_value,
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
    """Graham (P/E too rich) and DDM (no real yield) stay out. PEG joins: expected EPS
    growth of 33% is capped at 20%, so forward EPS 4.0 x (1.0 x 20) = 80."""
    report = build_valuation_report("NVDA", 150.0, expensive_growth_stock, ASSUMPTIONS)
    assert report.aggregated_values == [pytest.approx(3.0 * 18.0), pytest.approx(80.0)]
    assert report.median_fair_value == pytest.approx(67.0)


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


# ---- PEG (B1, B2) -------------------------------------------------------------------

def _growth(trailing, forward, **extra):
    return Fundamentals(
        symbol="G", values={"trailingEps": trailing, "forwardEps": forward, **extra}
    )


def test_implied_growth_is_forward_over_reported_eps():
    assert implied_earnings_growth(_growth(2.0, 2.5)) == pytest.approx(0.25)


@pytest.mark.parametrize("trailing, forward", [(0.0, 2.5), (-1.0, 2.5), (2.0, 0.0), (2.0, None), (None, 2.0)])
def test_implied_growth_is_unknown_without_two_positive_eps(trailing, forward):
    """A growth rate off a loss or a missing estimate would be a made-up number."""
    assert implied_earnings_growth(_growth(trailing, forward)) is None


def test_peg_is_forward_eps_times_peg_target_times_growth_percent():
    estimate = peg_value(_growth(2.0, 2.36), ASSUMPTIONS)  # 18% growth, under the cap
    assert estimate.is_applicable
    assert estimate.fair_value == pytest.approx(2.36 * 18.0)


def test_peg_target_scales_the_fair_multiple():
    generous = ValuationAssumptions(peg_target=1.5)
    assert peg_value(_growth(2.0, 2.36), generous).fair_value == pytest.approx(2.36 * 27.0)


def test_peg_caps_growth_because_it_does_not_last_forever():
    """60% expected growth is not extrapolated: the multiple stops at the cap (20)."""
    estimate = peg_value(_growth(2.0, 3.2), ASSUMPTIONS)
    assert estimate.fair_value == pytest.approx(3.2 * 20.0)
    capped = ValuationAssumptions(growth_cap=0.30)
    assert peg_value(_growth(2.0, 3.2), capped).fair_value == pytest.approx(3.2 * 30.0)


@pytest.mark.parametrize(
    "trailing, forward",
    [(2.0, 2.2), (2.0, 2.0), (2.0, 1.5), (-1.0, 3.0), (2.0, None), (None, None)],
)
def test_peg_stays_out_for_slow_negative_or_unknown_growth(trailing, forward):
    estimate = peg_value(_growth(trailing, forward), ASSUMPTIONS)
    assert not estimate.is_applicable
    assert estimate.fair_value is None
    assert not estimate.counts_toward_aggregate


# ---- DCF (E1, E2) -------------------------------------------------------------------

def _cash_machine(**overrides):
    values = {"freeCashflow": 1000.0, "sharesOutstanding": 100.0, "sector": "Technology"}
    values.update(overrides)
    return Fundamentals(symbol="C", values=values)


def test_dcf_with_no_growth_edge_equals_a_gordon_perpetuity():
    """With no EPS estimate, growth starts at the terminal rate, so the whole stream grows
    at 3% forever and the value has a closed form: FCF x (1+g) / (r-g)."""
    estimate = dcf_value(_cash_machine(), ASSUMPTIONS)
    assert estimate.fair_value == pytest.approx(10.0 * 1.03 / (0.09 - 0.03))


def test_dcf_matches_an_independent_year_by_year_calculation():
    fundamentals = _cash_machine(trailingEps=2.0, forwardEps=2.4)  # 20% growth, at the cap
    start, terminal, rate = 0.20, 0.03, 0.09
    growth_by_year = [start + (terminal - start) * y / (DCF_YEARS - 1) for y in range(DCF_YEARS)]
    flows, flow = [], 10.0
    for g in growth_by_year:
        flow *= 1 + g
        flows.append(flow)
    expected = sum(f / (1 + rate) ** (i + 1) for i, f in enumerate(flows))
    expected += flows[-1] * (1 + terminal) / (rate - terminal) / (1 + rate) ** DCF_YEARS
    assert dcf_value(fundamentals, ASSUMPTIONS).fair_value == pytest.approx(expected)


def test_dcf_uses_the_users_required_return_as_the_discount_rate():
    cautious = ValuationAssumptions(required_return=0.12)
    assert (
        dcf_value(_cash_machine(), cautious).fair_value
        < dcf_value(_cash_machine(), ASSUMPTIONS).fair_value
    )


def test_dcf_starting_growth_is_capped():
    wild = _cash_machine(trailingEps=1.0, forwardEps=5.0)  # 400% implied
    capped = _cash_machine(trailingEps=1.0, forwardEps=1.2)  # exactly the 20% cap
    assert dcf_value(wild, ASSUMPTIONS).fair_value == pytest.approx(
        dcf_value(capped, ASSUMPTIONS).fair_value
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"freeCashflow": 0.0},
        {"freeCashflow": -500.0},
        {"freeCashflow": None},
        {"sharesOutstanding": None},
        {"sharesOutstanding": 0.0},
        {"sector": "Financial Services"},
        {"sector": "Real Estate"},
    ],
)
def test_dcf_gates_out_what_it_cannot_value(overrides):
    estimate = dcf_value(_cash_machine(**overrides), ASSUMPTIONS)
    assert not estimate.is_applicable
    assert estimate.fair_value is None
    assert not estimate.counts_toward_aggregate


def test_dcf_gates_out_when_the_discount_rate_does_not_exceed_terminal_growth():
    broken = ValuationAssumptions(required_return=0.03, terminal_growth=0.03)
    assert not dcf_value(_cash_machine(), broken).is_applicable


# ---- the new models do not disturb what they do not apply to (B3, E3) ---------------

def test_value_stock_without_new_inputs_is_untouched_by_the_new_models(dividend_payer):
    report = build_valuation_report("KO", 60.0, dividend_payer, ASSUMPTIONS)
    by_name = {e.model_name: e for e in report.estimates}
    assert not by_name["PEG（成長調整）"].counts_toward_aggregate  # 12% growth < 15%
    assert not by_name["DCF（現金流折現）"].counts_toward_aggregate  # no free cash flow
    assert len(report.aggregated_values) == 3


# ---- a growth stock gets more than one model (B4) -----------------------------------

def test_growth_stock_is_no_longer_a_single_assumption(expensive_growth_stock):
    """High P/E, no real dividend, fast growth: Graham and DDM drop out, which used to
    leave Target P/E alone. PEG now gives it a second, growth-aware view."""
    report = build_valuation_report("NVDA", 150.0, expensive_growth_stock, ASSUMPTIONS)
    assert len(report.aggregated_values) >= 2
    assert report.median_fair_value > 3.0 * 18.0


def test_new_models_are_listed_even_when_not_applicable(dividend_payer):
    """B5: the estimate table is generated from the estimates, so no UI change is needed."""
    names = [e.model_name for e in build_valuation_report("KO", 60.0, dividend_payer, ASSUMPTIONS).estimates]
    assert "PEG（成長調整）" in names and "DCF（現金流折現）" in names


def test_analyst_target_still_never_counts_with_the_new_models(expensive_growth_stock):
    report = build_valuation_report("NVDA", 150.0, expensive_growth_stock, ASSUMPTIONS)
    assert 200.0 not in report.aggregated_values


# ---- the margin the report applies (D2, D3, D4) -------------------------------------

def test_well_supported_value_keeps_the_configured_margin(dividend_payer):
    report = build_valuation_report("KO", 60.0, dividend_payer, ASSUMPTIONS)
    assert report.uncertainty().reasons == ()
    assert report.effective_margin_of_safety(ASSUMPTIONS) == pytest.approx(0.20)
    assert report.buy_below_price(ASSUMPTIONS) == pytest.approx(report.median_fair_value * 0.8)


def test_a_lone_model_widens_the_margin_by_default():
    lone = Fundamentals(symbol="X", values={"trailingEps": 3.0, "forwardEps": 3.1})
    report = build_valuation_report("X", 50.0, lone, ASSUMPTIONS)
    assert len(report.aggregated_values) == 1
    assert report.effective_margin_of_safety(ASSUMPTIONS) == pytest.approx(0.40)
    assert report.buy_below_price(ASSUMPTIONS) == pytest.approx(54.0 * 0.6)


def test_a_margin_the_user_set_is_not_widened():
    lone = Fundamentals(symbol="X", values={"trailingEps": 3.0, "forwardEps": 3.1})
    chosen = ValuationAssumptions(margin_of_safety=0.10, adaptive_margin=False)
    report = build_valuation_report("X", 50.0, lone, chosen)
    assert report.effective_margin_of_safety(chosen) == pytest.approx(0.10)
    assert report.buy_below_price(chosen) == pytest.approx(54.0 * 0.9)
