import pytest

from portfolio.entry_verdict import (
    HINT_CHECK_INPUTS,
    HINT_CONSIDER,
    HINT_INSUFFICIENT,
    HINT_NOT_YOUR_PRICE,
    HINT_WATCH,
    TIER_ABOVE_VALUE,
    TIER_AT_MARGIN,
    TIER_BELOW_VALUE,
    TIER_IMPLAUSIBLE,
    VerdictInputs,
    build_entry_verdict,
    classify_tier,
)

MARGIN = 0.20
ROBUST = 3


@pytest.mark.parametrize(
    "upside,expected",
    [
        (-30.0, TIER_ABOVE_VALUE),
        (-0.1, TIER_ABOVE_VALUE),
        (0.0, TIER_BELOW_VALUE),
        (19.9, TIER_BELOW_VALUE),
        (20.0, TIER_AT_MARGIN),
        (39.9, TIER_AT_MARGIN),
        (40.0, TIER_IMPLAUSIBLE),
        (70.0, TIER_IMPLAUSIBLE),
    ],
)
def test_tier_boundaries_follow_the_users_margin_of_safety(upside, expected):
    assert classify_tier(upside, MARGIN) == expected


def test_a_tighter_margin_moves_the_boundaries_with_it():
    # The tiers are defined relative to the user's own buffer, not fixed percentages.
    assert classify_tier(12.0, 0.10) == TIER_AT_MARGIN
    assert classify_tier(12.0, 0.20) == TIER_BELOW_VALUE


@pytest.mark.parametrize(
    "upside,expected",
    [
        (-30.0, HINT_NOT_YOUR_PRICE),
        (10.0, HINT_WATCH),
        (25.0, HINT_CONSIDER),
        (70.0, HINT_CHECK_INPUTS),
    ],
)
def test_action_hints_are_offered_when_several_models_agree(upside, expected):
    verdict = build_entry_verdict(VerdictInputs(upside, MARGIN, ROBUST))
    assert verdict.action_hint == expected
    assert verdict.has_action_hint
    assert not verdict.is_fragile


@pytest.mark.parametrize("model_count", [0, 1])
def test_a_single_model_gets_a_tier_but_no_action_hint(model_count):
    """MSFT's 'undervalued' verdict rests on one assumption; wording it as an action
    would dress a guess up as a finding."""
    verdict = build_entry_verdict(VerdictInputs(25.0, MARGIN, model_count))
    assert verdict.tier == TIER_AT_MARGIN  # the gap is still reported
    assert verdict.action_hint == HINT_INSUFFICIENT
    assert verdict.is_fragile
    assert not verdict.has_action_hint


def test_two_agreeing_models_are_enough_to_stop_being_fragile():
    assert not build_entry_verdict(VerdictInputs(25.0, MARGIN, 2)).is_fragile


def test_no_reference_price_yields_no_verdict_rather_than_a_neutral_one():
    assert build_entry_verdict(VerdictInputs(None, MARGIN, 0)) is None
