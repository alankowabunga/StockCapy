"""D1/D2/D4: how shaky a fair value is, and how that widens (never narrows) the cushion."""
import pytest

from portfolio.uncertainty import (
    REASON_DISAGREEMENT,
    REASON_EARNINGS_FLUX,
    REASON_SINGLE_MODEL,
    TIER_EXTREME,
    TIER_HIGH,
    TIER_LOW,
    TIER_MEDIUM,
    assess_uncertainty,
)


def test_agreeing_models_and_steady_earnings_are_low_uncertainty():
    assessment = assess_uncertainty(3, 20.0, 0.10)
    assert assessment.tier == TIER_LOW
    assert assessment.reasons == ()


def test_a_lone_model_is_never_low_uncertainty():
    assessment = assess_uncertainty(1, 0.0, 0.10)
    assert assessment.reasons == (REASON_SINGLE_MODEL,)
    assert assessment.tier == TIER_HIGH  # cannot be cross-checked at all


def test_models_far_apart_raise_it_one_step():
    assessment = assess_uncertainty(3, 80.0, 0.10)
    assert assessment.reasons == (REASON_DISAGREEMENT,)
    assert assessment.tier == TIER_MEDIUM


def test_violent_earnings_change_raises_it_in_either_direction():
    assert assess_uncertainty(3, 10.0, 0.55).reasons == (REASON_EARNINGS_FLUX,)
    assert assess_uncertainty(3, 10.0, -0.45).reasons == (REASON_EARNINGS_FLUX,)
    assert assess_uncertainty(3, 10.0, 0.55).tier == TIER_MEDIUM


def test_unknown_growth_is_not_treated_as_flux():
    """No estimate is missing information, not evidence of instability."""
    assert assess_uncertainty(3, 10.0, None).tier == TIER_LOW


def test_disagreement_needs_at_least_two_models_to_mean_anything():
    assert REASON_DISAGREEMENT not in assess_uncertainty(1, 500.0, None).reasons


def test_all_four_tiers_are_reachable():
    tiers = {
        assess_uncertainty(3, 10.0, 0.1).tier,          # no flags
        assess_uncertainty(3, 90.0, 0.1).tier,          # disagreement
        assess_uncertainty(3, 90.0, 0.6).tier,          # disagreement + flux
        assess_uncertainty(1, 0.0, 0.6).tier,           # single model + flux
    }
    assert tiers == {TIER_LOW, TIER_MEDIUM, TIER_HIGH, TIER_EXTREME}


def test_low_tier_leaves_the_users_margin_alone():
    """D4: nothing about a well-supported value should change."""
    assessment = assess_uncertainty(3, 10.0, 0.1)
    assert assessment.effective_margin(0.20) == pytest.approx(0.20)
    assert assessment.effective_margin(0.15) == pytest.approx(0.15)


def test_higher_tiers_widen_the_margin_up_to_their_floor():
    assert assess_uncertainty(3, 90.0, 0.1).effective_margin(0.20) == pytest.approx(0.30)
    assert assess_uncertainty(3, 90.0, 0.6).effective_margin(0.20) == pytest.approx(0.40)
    assert assess_uncertainty(1, 0.0, 0.6).effective_margin(0.20) == pytest.approx(0.50)


def test_a_margin_already_wider_than_the_floor_is_never_narrowed():
    assert assess_uncertainty(3, 90.0, 0.1).effective_margin(0.45) == pytest.approx(0.45)


def test_an_explicit_margin_is_respected_exactly_even_when_the_value_is_shaky():
    """D2: a margin the user typed is theirs."""
    shaky = assess_uncertainty(1, 0.0, 0.6)
    assert shaky.effective_margin(0.10, adaptive=False) == pytest.approx(0.10)
