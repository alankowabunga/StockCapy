"""The user cannot say what multiple they want, so the tool offers sourced options and
shows the whole consequence of each. It must never invent an option it cannot defend."""
import pytest

from conftest import make_settings  # noqa: F401  (keeps fixture discovery consistent)
from portfolio.entry_verdict import HINT_INSUFFICIENT, TIER_ABOVE_VALUE, TIER_AT_MARGIN
from portfolio.historical_pe import HistoricalPriceEarnings
from portfolio.market import Fundamentals
from portfolio.target_pe_options import (
    CONSERVATIVE_PE,
    GROWTH_PE,
    HISTORICAL_LABEL,
    NEUTRAL_PE,
    CURRENT_LABEL,
    TargetPeContext,
    build_target_pe_choices,
    describe_historical,
)
from portfolio.valuation import ValuationAssumptions

PRICE = 490.0
# Trailing EPS 17.94 and P/E 27.3 mirror the real MSFT numbers this was built against.
MSFT = Fundamentals(
    symbol="MSFT",
    values={"trailingEps": 17.94, "forwardEps": 23.59, "bookValue": 59.57,
            "trailingPE": 27.33, "dividendYield": 0.73, "sector": "Technology"},
)


def _context(target_pe: float = 18.0) -> TargetPeContext:
    return TargetPeContext(
        symbol="MSFT",
        last_price=PRICE,
        fundamentals=MSFT,
        assumptions=ValuationAssumptions(target_price_earnings=target_pe),
    )


def test_three_presets_are_always_offered():
    choices = build_target_pe_choices(_context(), historical=None)
    assert [c.target_pe for c in choices.candidates if c.label != CURRENT_LABEL] == [
        CONSERVATIVE_PE, NEUTRAL_PE, GROWTH_PE
    ]


def test_each_option_previews_the_price_it_would_produce():
    choices = build_target_pe_choices(_context(), historical=None)
    growth = next(c for c in choices.candidates if c.target_pe == GROWTH_PE)
    # Trailing EPS, not forward: the multiple is a trailing-basis number. The PEG model
    # also applies (31% expected growth, capped at 20%: forward EPS 23.59 x 20), so the
    # reference is the median of the two. The two agree within a few percent, so the
    # margin stays at the user's 20%.
    target_pe_value = 17.94 * GROWTH_PE
    assert growth.fair_value == pytest.approx((target_pe_value + 23.59 * 20.0) / 2)
    assert growth.buy_below == pytest.approx(growth.fair_value * 0.8)


def test_each_option_says_what_it_claims_against_the_market_price():
    """A multiple is a claim about the market being wrong; showing it as a premium to the
    current P/E is what makes that claim legible."""
    choices = build_target_pe_choices(_context(), historical=None)
    conservative = next(c for c in choices.candidates if c.target_pe == CONSERVATIVE_PE)
    assert conservative.premium_to_market_pe_percent == pytest.approx(
        (CONSERVATIVE_PE / 27.33 - 1) * 100
    )


def test_a_cheap_multiple_reads_as_above_value_and_a_rich_one_reaches_the_margin():
    choices = build_target_pe_choices(_context(), historical=None)
    by_pe = {c.target_pe: c for c in choices.candidates}
    assert by_pe[CONSERVATIVE_PE].verdict.tier == TIER_ABOVE_VALUE
    assert by_pe[GROWTH_PE].verdict.tier == TIER_ABOVE_VALUE  # ~460 < 490, still above
    # Median of (17.94 x 45, PEG 471.8) = ~640, 30% above the price: past the 20% margin.
    richer = build_target_pe_choices(_context(target_pe=45.0), historical=None)
    current = next(c for c in richer.candidates if c.label == CURRENT_LABEL)
    assert current.verdict.tier == TIER_AT_MARGIN


SLOW_GROWER = Fundamentals(
    symbol="MSFT",
    values={"trailingEps": 17.94, "forwardEps": 18.5, "bookValue": 59.57,
            "trailingPE": 27.33, "dividendYield": 0.73, "sector": "Technology"},
)


def test_no_option_carries_an_action_hint_while_a_single_model_applies():
    """Expected growth under 15% keeps PEG out, so only Target P/E applies."""
    context = TargetPeContext(
        symbol="MSFT",
        last_price=PRICE,
        fundamentals=SLOW_GROWER,
        assumptions=ValuationAssumptions(),
    )
    choices = build_target_pe_choices(context, historical=None)
    assert all(c.verdict.action_hint == HINT_INSUFFICIENT for c in choices.candidates)
    assert all(c.model_count == 1 for c in choices.candidates)


def test_a_growth_stock_has_two_models_so_the_options_can_carry_a_hint():
    choices = build_target_pe_choices(_context(), historical=None)
    assert all(c.model_count == 2 for c in choices.candidates)
    assert all(not c.verdict.is_fragile for c in choices.candidates)


def test_a_stable_history_becomes_a_selectable_option():
    stable = HistoricalPriceEarnings("MSFT", 35.3, 30.0, 40.0, 900)
    choices = build_target_pe_choices(_context(), historical=stable)
    historical = next(c for c in choices.candidates if c.label == HISTORICAL_LABEL)
    assert historical.target_pe == pytest.approx(35.3)
    assert choices.historical_note == ""


def test_an_unstable_history_is_withheld_and_the_reason_is_stated():
    """NVDA's real numbers: median 73.8 with quartiles 46-134. Offering it would be
    false precision; omitting it silently would look like a missing feature."""
    unstable = HistoricalPriceEarnings("NVDA", 73.8, 46.0, 134.0, 910)
    choices = build_target_pe_choices(_context(), historical=unstable)
    assert all(c.label != HISTORICAL_LABEL for c in choices.candidates)
    assert "沒有穩定水位" in choices.historical_note
    assert "73.8" in choices.historical_note


def test_a_missing_history_explains_itself_too():
    assert "取不到足夠的歷史盈餘" in describe_historical(None)


def test_the_multiple_in_effect_is_always_shown_even_when_it_matches_no_preset():
    choices = build_target_pe_choices(_context(target_pe=18.0), historical=None)
    current = [c for c in choices.candidates if c.is_current]
    assert len(current) == 1
    assert current[0].target_pe == pytest.approx(18.0)


def test_a_preset_that_is_already_in_effect_is_not_duplicated():
    choices = build_target_pe_choices(_context(target_pe=NEUTRAL_PE), historical=None)
    assert sum(1 for c in choices.candidates if c.is_current) == 1
    assert [c.label for c in choices.candidates].count(CURRENT_LABEL) == 0


def test_options_are_ordered_by_multiple_so_the_ladder_reads_in_one_direction():
    stable = HistoricalPriceEarnings("MSFT", 35.3, 30.0, 40.0, 900)
    choices = build_target_pe_choices(_context(), historical=stable)
    multiples = [c.target_pe for c in choices.candidates]
    assert multiples == sorted(multiples)
