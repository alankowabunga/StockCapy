from __future__ import annotations

from dataclasses import dataclass

TIER_LOW = "低"
TIER_MEDIUM = "中"
TIER_HIGH = "高"
TIER_EXTREME = "極高"

REASON_SINGLE_MODEL = "只有單一模型適用"
REASON_DISAGREEMENT = "模型之間差距很大"
REASON_EARNINGS_FLUX = "盈餘變動劇烈"

# A spread wider than this share of the median means the models are not describing the
# same company — the median then hides a disagreement rather than resolving it.
DISAGREEMENT_THRESHOLD_PERCENT = 75.0

# Expected EPS differing from reported EPS by this much means trailing figures describe
# a business that no longer exists (the same condition that makes trailing P/E unstable).
EARNINGS_FLUX_THRESHOLD = 0.40

MIN_ROBUST_MODEL_COUNT = 2

# A lone model cannot be cross-checked at all, so it weighs double. (It also cannot
# coexist with "disagreement", which needs two models, so equal weights would leave the
# top tier unreachable.)
POINTS_SINGLE_MODEL = 2
POINTS_DISAGREEMENT = 1
POINTS_EARNINGS_FLUX = 1

# More uncertain values demand a wider cushion before the price counts as a bargain.
# Same idea as Morningstar's uncertainty-scaled margin; the numbers are this tool's own
# choices. The user's margin is never narrowed, only widened up to the floor.
_TIER_BY_POINTS = (TIER_LOW, TIER_MEDIUM, TIER_HIGH, TIER_EXTREME)
MARGIN_FLOOR_BY_TIER = {
    TIER_LOW: 0.0,
    TIER_MEDIUM: 0.30,
    TIER_HIGH: 0.40,
    TIER_EXTREME: 0.50,
}


@dataclass(frozen=True)
class UncertaintyAssessment:
    """How much the fair value rests on assumptions rather than agreement. Describes
    the estimate, not the company — it never says whether to buy."""

    tier: str
    reasons: tuple[str, ...]

    @property
    def margin_floor(self) -> float:
        return MARGIN_FLOOR_BY_TIER[self.tier]

    def effective_margin(self, user_margin: float, adaptive: bool = True) -> float:
        """The user's margin, widened to this tier's floor. An explicitly chosen margin
        (adaptive=False) is respected exactly."""
        if not adaptive:
            return user_margin
        return max(user_margin, self.margin_floor)


def assess_uncertainty(
    model_count: int,
    disagreement_percent: float | None,
    earnings_growth: float | None,
) -> UncertaintyAssessment:
    reasons: list[str] = []
    points = 0
    if model_count < MIN_ROBUST_MODEL_COUNT:
        reasons.append(REASON_SINGLE_MODEL)
        points += POINTS_SINGLE_MODEL
    if (
        model_count >= MIN_ROBUST_MODEL_COUNT
        and disagreement_percent is not None
        and disagreement_percent >= DISAGREEMENT_THRESHOLD_PERCENT
    ):
        reasons.append(REASON_DISAGREEMENT)
        points += POINTS_DISAGREEMENT
    if earnings_growth is not None and abs(earnings_growth) >= EARNINGS_FLUX_THRESHOLD:
        reasons.append(REASON_EARNINGS_FLUX)
        points += POINTS_EARNINGS_FLUX
    return UncertaintyAssessment(
        tier=_TIER_BY_POINTS[min(points, len(_TIER_BY_POINTS) - 1)], reasons=tuple(reasons)
    )
