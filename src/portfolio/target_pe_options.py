from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .entry_verdict import EntryVerdict, VerdictInputs, build_entry_verdict
from .historical_pe import HistoricalPriceEarnings
from .market import Fundamentals
from .valuation import ValuationAssumptions, build_valuation_report

CONSERVATIVE_PE = 15.0
NEUTRAL_PE = 20.0
GROWTH_PE = 25.0

HISTORICAL_LABEL = "該股歷史水位"
CURRENT_LABEL = "目前設定"
CURRENT_EPSILON = 1e-9

# Stated as conventions the user is choosing between, not as facts about this stock.
PRESETS = (
    (CONSERVATIVE_PE, "保守", "要求比市場現在更便宜才進場"),
    (NEUTRAL_PE, "中性", "常用的中間值，不特別看多或看空"),
    (GROWTH_PE, "成長股", "願意為持續成長多付一些"),
)


@dataclass(frozen=True)
class TargetPeCandidate:
    """One selectable multiple with the whole consequence of picking it, so the choice
    is made against outcomes rather than against a bare number."""

    label: str
    rationale: str
    target_pe: float
    is_current: bool
    fair_value: float | None
    buy_below: float | None
    upside_percent: float | None
    model_count: int
    verdict: EntryVerdict | None
    premium_to_market_pe_percent: float | None

    @property
    def is_usable(self) -> bool:
        return self.fair_value is not None


@dataclass(frozen=True)
class TargetPeChoices:
    symbol: str
    last_price: float
    trailing_pe: float | None
    candidates: list[TargetPeCandidate]
    historical_note: str


def _premium_to_market(target_pe: float, trailing_pe: float | None) -> float | None:
    """How much more (or less) than the market's current multiple this asks for — the
    number that says what the choice actually claims."""
    if not trailing_pe:
        return None
    return (target_pe / trailing_pe - 1) * 100


@dataclass(frozen=True)
class TargetPeContext:
    """The per-symbol inputs every candidate shares, so they cannot drift apart."""

    symbol: str
    last_price: float
    fundamentals: Fundamentals
    assumptions: ValuationAssumptions

    @property
    def trailing_pe(self) -> float | None:
        return self.fundamentals.number("trailingPE")


@dataclass(frozen=True)
class ValuationPreview:
    fair_value: float | None
    buy_below: float | None
    upside_percent: float | None
    model_count: int
    margin_of_safety: float


def _preview(context: TargetPeContext, assumptions: ValuationAssumptions) -> ValuationPreview:
    """Rebuilds the whole report, not just the Target P/E model: the reference price is
    a median across applicable models, so a new multiple can move it by less than itself."""
    report = build_valuation_report(
        context.symbol, context.last_price, context.fundamentals, assumptions
    )
    return ValuationPreview(
        fair_value=report.median_fair_value,
        buy_below=report.buy_below_price(assumptions),
        upside_percent=report.upside_percent(),
        model_count=len(report.aggregated_values),
        margin_of_safety=(
            report.effective_margin_of_safety(assumptions)
            if report.aggregated_values
            else assumptions.margin_of_safety
        ),
    )


def _candidate(
    label: str, rationale: str, target_pe: float, context: TargetPeContext
) -> TargetPeCandidate:
    assumptions = dataclasses.replace(context.assumptions, target_price_earnings=target_pe)
    preview = _preview(context, assumptions)
    return TargetPeCandidate(
        label=label,
        rationale=rationale,
        target_pe=target_pe,
        is_current=abs(target_pe - context.assumptions.target_price_earnings) < CURRENT_EPSILON,
        fair_value=preview.fair_value,
        buy_below=preview.buy_below,
        upside_percent=preview.upside_percent,
        model_count=preview.model_count,
        verdict=build_entry_verdict(
            VerdictInputs(preview.upside_percent, preview.margin_of_safety, preview.model_count)
        ),
        premium_to_market_pe_percent=_premium_to_market(target_pe, context.trailing_pe),
    )


def _historical_candidate(
    context: TargetPeContext, historical: HistoricalPriceEarnings | None
) -> TargetPeCandidate | None:
    if historical is None or not historical.is_usable or not historical.is_stable:
        return None
    return _candidate(
        HISTORICAL_LABEL,
        f"這檔股票自己 5 年來的 P/E 中位數（{historical.median_pe:,.1f}）",
        historical.median_pe,
        context,
    )


def describe_historical(historical: HistoricalPriceEarnings | None) -> str:
    """Says why the stock's own history is unavailable, instead of just omitting it."""
    if historical is None or not historical.is_usable:
        return "取不到足夠的歷史盈餘，無法算出這檔的歷史 P/E 水位。"
    if historical.is_stable:
        return ""
    return (
        f"歷史 P/E 中位數 {historical.median_pe:,.1f}，但四分位距落在 "
        f"{historical.lower_quartile_pe:,.1f}–{historical.upper_quartile_pe:,.1f}，"
        "擺盪太大代表這檔的 P/E 沒有穩定水位，拿它當基準是假精確，故不列為選項。"
    )


def _current_candidate(context: TargetPeContext, chosen: list[TargetPeCandidate]) -> list:
    """Keeps whatever is in effect visible even when it matches no preset, so the
    comparison always includes the status quo."""
    if any(candidate.is_current for candidate in chosen):
        return []
    in_effect = context.assumptions.target_price_earnings
    return [_candidate(CURRENT_LABEL, "你目前生效的倍數", in_effect, context)]


def build_target_pe_choices(
    context: TargetPeContext, historical: HistoricalPriceEarnings | None
) -> TargetPeChoices:
    candidates = [
        _candidate(label, rationale, target_pe, context)
        for target_pe, label, rationale in PRESETS
    ]
    historical_candidate = _historical_candidate(context, historical)
    if historical_candidate is not None:
        candidates.append(historical_candidate)
    candidates.extend(_current_candidate(context, candidates))
    candidates.sort(key=lambda candidate: candidate.target_pe)
    return TargetPeChoices(
        symbol=context.symbol,
        last_price=context.last_price,
        trailing_pe=context.trailing_pe,
        candidates=candidates,
        historical_note=describe_historical(historical),
    )
