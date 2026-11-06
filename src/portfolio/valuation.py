from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median

from .market import Fundamentals

GRAHAM_MULTIPLIER = 22.5
GRAHAM_MAX_PE = 25.0
MIN_DIVIDEND_YIELD_PERCENT = 2.0


@dataclass(frozen=True)
class ValuationAssumptions:
    """User-owned inputs. Every number below is a choice, not a fact."""

    target_price_earnings: float = 18.0
    required_return: float = 0.09
    dividend_growth: float = 0.04
    margin_of_safety: float = 0.20


@dataclass(frozen=True)
class ValuationEstimate:
    model_name: str
    fair_value: float | None
    assumptions: dict[str, float]
    note: str
    is_applicable: bool = True
    is_informational: bool = False

    @property
    def has_value(self) -> bool:
        return self.fair_value is not None and self.fair_value > 0

    @property
    def counts_toward_aggregate(self) -> bool:
        return self.has_value and self.is_applicable and not self.is_informational


@dataclass
class ValuationReport:
    symbol: str
    last_price: float
    estimates: list[ValuationEstimate] = field(default_factory=list)

    @property
    def aggregated_values(self) -> list[float]:
        return [
            estimate.fair_value
            for estimate in self.estimates
            if estimate.counts_toward_aggregate
        ]  # type: ignore[misc]

    @property
    def median_fair_value(self) -> float | None:
        values = self.aggregated_values
        return median(values) if values else None

    @property
    def value_range(self) -> tuple[float, float] | None:
        values = self.aggregated_values
        return (min(values), max(values)) if values else None

    def buy_below_price(self, assumptions: ValuationAssumptions) -> float | None:
        midpoint = self.median_fair_value
        if midpoint is None:
            return None
        return midpoint * (1 - assumptions.margin_of_safety)

    def upside_percent(self) -> float | None:
        midpoint = self.median_fair_value
        if midpoint is None or not self.last_price:
            return None
        return (midpoint / self.last_price - 1) * 100

    def model_disagreement_percent(self) -> float | None:
        spread = self.value_range
        midpoint = self.median_fair_value
        if spread is None or not midpoint:
            return None
        return (spread[1] - spread[0]) / midpoint * 100


def graham_number(fundamentals: Fundamentals) -> ValuationEstimate:
    earnings = fundamentals.number("trailingEps") or 0.0
    book_value = fundamentals.number("bookValue") or 0.0
    price_earnings = fundamentals.number("trailingPE") or 0.0
    has_inputs = earnings > 0 and book_value > 0
    value = math.sqrt(GRAHAM_MULTIPLIER * earnings * book_value) if has_inputs else None
    return ValuationEstimate(
        model_name="Graham Number",
        fair_value=value,
        assumptions={"eps": earnings, "bookValuePerShare": book_value},
        note="價值股篩選用；輕資產或高成長股會被系統性低估，故 P/E>25 時排除",
        is_applicable=has_inputs and 0 < price_earnings <= GRAHAM_MAX_PE,
    )


@dataclass(frozen=True)
class EarningsForMultiple:
    """Which EPS the multiple gets applied to. A trailing-basis multiple — what
    historical_pe computes and what "P/E" conventionally means — must meet trailing
    EPS, or the growth already priced into the multiple is counted a second time."""

    eps: float
    is_trailing: bool

    @property
    def has_earnings(self) -> bool:
        return self.eps > 0

    @property
    def note(self) -> str:
        if not self.has_earnings:
            return "取不到每股盈餘，無法套用倍數"
        if self.is_trailing:
            return "結論完全由你設的 target P/E 決定，換個倍數答案就不同"
        return (
            "缺 trailing EPS，改用預估 EPS；倍數是 trailing 基準，"
            "兩者混用會把成長算兩次，這個估值偏高"
        )


def earnings_for_multiple(fundamentals: Fundamentals) -> EarningsForMultiple:
    trailing = fundamentals.number("trailingEps") or 0.0
    if trailing > 0:
        return EarningsForMultiple(trailing, is_trailing=True)
    return EarningsForMultiple(fundamentals.number("forwardEps") or 0.0, is_trailing=False)


def target_multiple_value(
    fundamentals: Fundamentals, assumptions: ValuationAssumptions
) -> ValuationEstimate:
    earnings = earnings_for_multiple(fundamentals)
    value = earnings.eps * assumptions.target_price_earnings if earnings.has_earnings else None
    return ValuationEstimate(
        model_name="Target P/E",
        fair_value=value,
        assumptions={"eps": earnings.eps, "targetPE": assumptions.target_price_earnings},
        note=earnings.note,
        is_applicable=earnings.has_earnings,
    )


def gordon_growth_value(
    fundamentals: Fundamentals, assumptions: ValuationAssumptions
) -> ValuationEstimate:
    dividend = fundamentals.number("dividendRate") or 0.0
    yield_percent = fundamentals.number("dividendYield") or 0.0
    spread = assumptions.required_return - assumptions.dividend_growth
    value = dividend * (1 + assumptions.dividend_growth) / spread if dividend > 0 and spread > 0 else None
    return ValuationEstimate(
        model_name="Gordon Growth (DDM)",
        fair_value=value,
        assumptions={
            "annualDividend": dividend,
            "requiredReturn": assumptions.required_return,
            "dividendGrowth": assumptions.dividend_growth,
        },
        note=f"僅適用殖利率 >= {MIN_DIVIDEND_YIELD_PERCENT}% 的穩定配息股；g 逼近 r 時會爆掉",
        is_applicable=value is not None and yield_percent >= MIN_DIVIDEND_YIELD_PERCENT,
    )


def analyst_consensus_value(fundamentals: Fundamentals) -> ValuationEstimate:
    return ValuationEstimate(
        model_name="Analyst Consensus",
        fair_value=fundamentals.number("targetMeanPrice"),
        assumptions={"analystCount": fundamentals.number("numberOfAnalystOpinions") or 0.0},
        note="這是賣方平均目標價，別人的看法，不是估值模型；僅列作對照",
        is_informational=True,
    )


def build_valuation_report(
    symbol: str,
    last_price: float,
    fundamentals: Fundamentals,
    assumptions: ValuationAssumptions,
) -> ValuationReport:
    estimates = [
        graham_number(fundamentals),
        target_multiple_value(fundamentals, assumptions),
        gordon_growth_value(fundamentals, assumptions),
        analyst_consensus_value(fundamentals),
    ]
    return ValuationReport(symbol=symbol, last_price=last_price, estimates=estimates)
