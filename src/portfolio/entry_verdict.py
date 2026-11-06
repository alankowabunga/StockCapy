from __future__ import annotations

from dataclasses import dataclass

# Below this many agreeing models the gap to fair value says more about the assumption
# than about the stock, so no action wording is offered at all.
MIN_ROBUST_MODEL_COUNT = 2

# Beyond this multiple of the margin of safety the gap is usually a broken assumption
# rather than an opportunity, so the tier says to check the inputs.
IMPLAUSIBLE_MARGIN_MULTIPLE = 2.0

TIER_ABOVE_VALUE = "高於估值"
TIER_BELOW_VALUE = "低於估值，未達安全邊際"
TIER_AT_MARGIN = "已達你設定的安全邊際"
TIER_IMPLAUSIBLE = "遠低於估值 —— 先確認假設"

HINT_NOT_YOUR_PRICE = "不符合你的條件"
HINT_WATCH = "可觀察"
HINT_CONSIDER = "可考慮進場"
HINT_CHECK_INPUTS = "先檢查假設，差距大到不尋常"
HINT_INSUFFICIENT = "證據不足，不給建議"

_HINT_BY_TIER = {
    TIER_ABOVE_VALUE: HINT_NOT_YOUR_PRICE,
    TIER_BELOW_VALUE: HINT_WATCH,
    TIER_AT_MARGIN: HINT_CONSIDER,
    TIER_IMPLAUSIBLE: HINT_CHECK_INPUTS,
}


@dataclass(frozen=True)
class VerdictInputs:
    """The three things a verdict needs, kept together so they cannot be mismatched."""

    upside_percent: float | None
    margin_of_safety: float
    model_count: int


@dataclass(frozen=True)
class EntryVerdict:
    """How far the price sits from the user's own line, and how much that line rests
    on. The gap alone is not interpretable: the same -50% means opposite things with
    one fragile model versus three agreeing ones."""

    tier: str
    action_hint: str
    is_fragile: bool
    model_count: int

    @property
    def has_action_hint(self) -> bool:
        return self.action_hint != HINT_INSUFFICIENT


def classify_tier(upside_percent: float, margin_of_safety: float) -> str:
    margin = margin_of_safety * 100
    if upside_percent < 0:
        return TIER_ABOVE_VALUE
    if upside_percent < margin:
        return TIER_BELOW_VALUE
    if upside_percent < margin * IMPLAUSIBLE_MARGIN_MULTIPLE:
        return TIER_AT_MARGIN
    return TIER_IMPLAUSIBLE


def build_entry_verdict(inputs: VerdictInputs) -> EntryVerdict | None:
    """None when there is no reference price at all — that is not a neutral verdict,
    it means the tool cannot value this symbol and must say so elsewhere."""
    if inputs.upside_percent is None:
        return None
    tier = classify_tier(inputs.upside_percent, inputs.margin_of_safety)
    is_fragile = inputs.model_count < MIN_ROBUST_MODEL_COUNT
    return EntryVerdict(
        tier=tier,
        action_hint=HINT_INSUFFICIENT if is_fragile else _HINT_BY_TIER[tier],
        is_fragile=is_fragile,
        model_count=inputs.model_count,
    )
