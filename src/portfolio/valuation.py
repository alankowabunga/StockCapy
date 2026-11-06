from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median

from .market import Fundamentals
from .uncertainty import UncertaintyAssessment, assess_uncertainty

GRAHAM_MULTIPLIER = 22.5
GRAHAM_MAX_PE = 25.0
MIN_DIVIDEND_YIELD_PERCENT = 2.0

# PEG is a growth-stock tool: for slow growers "multiple = growth" yields absurdly low
# multiples, so below this expected growth the model stays out.
MIN_PEG_GROWTH = 0.15
DCF_YEARS = 10
# Cash-flow models misread these: bank cash flow is mostly lending, and REIT free cash
# flow is understated by depreciation on property that does not wear out.
DCF_EXCLUDED_SECTORS = frozenset({"Financial Services", "Real Estate"})


@dataclass(frozen=True)
class ValuationAssumptions:
    """User-owned inputs. Every number below is a choice, not a fact."""

    target_price_earnings: float = 18.0
    required_return: float = 0.09
    dividend_growth: float = 0.04
    margin_of_safety: float = 0.20
    # Growth-model choices. Code-level defaults, not stored per symbol (yet).
    peg_target: float = 1.0
    growth_cap: float = 0.20
    terminal_growth: float = 0.03
    # True until the user states a margin themselves; then it is theirs, exactly.
    adaptive_margin: bool = True


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
    earnings_growth: float | None = None

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

    def uncertainty(self) -> UncertaintyAssessment:
        return assess_uncertainty(
            len(self.aggregated_values),
            self.model_disagreement_percent(),
            self.earnings_growth,
        )

    def effective_margin_of_safety(self, assumptions: ValuationAssumptions) -> float:
        """The user's margin, widened when the fair value is shaky (see uncertainty.py)."""
        return self.uncertainty().effective_margin(
            assumptions.margin_of_safety, assumptions.adaptive_margin
        )

    def buy_below_price(self, assumptions: ValuationAssumptions) -> float | None:
        midpoint = self.median_fair_value
        if midpoint is None:
            return None
        return midpoint * (1 - self.effective_margin_of_safety(assumptions))

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


def implied_earnings_growth(fundamentals: Fundamentals) -> float | None:
    """Next-year expected EPS over reported EPS, minus one. None unless both are positive:
    a growth rate off a loss or a missing estimate would be a made-up number."""
    trailing = fundamentals.number("trailingEps") or 0.0
    forward = fundamentals.number("forwardEps") or 0.0
    if trailing <= 0 or forward <= 0:
        return None
    return forward / trailing - 1


def peg_value(
    fundamentals: Fundamentals, assumptions: ValuationAssumptions
) -> ValuationEstimate:
    """Growth-adjusted multiple: fair forward P/E = PEG target x growth (in percent).
    Pairs forward EPS with a forward-looking multiple, so growth is counted once."""
    growth = implied_earnings_growth(fundamentals)
    forward_eps = fundamentals.number("forwardEps") or 0.0
    applicable = growth is not None and growth >= MIN_PEG_GROWTH
    capped = min(growth, assumptions.growth_cap) if applicable else None
    fair_pe = assumptions.peg_target * capped * 100 if capped is not None else None
    return ValuationEstimate(
        model_name="PEG（成長調整）",
        fair_value=forward_eps * fair_pe if fair_pe is not None else None,
        assumptions={
            "forwardEps": forward_eps,
            "impliedGrowth": growth or 0.0,
            "growthUsed": capped or 0.0,
            "pegTarget": assumptions.peg_target,
        },
        note=(
            f"僅適用預期 EPS 成長 >= {MIN_PEG_GROWTH:.0%} 的成長股；"
            f"成長率上限 {assumptions.growth_cap:.0%}（成長不會永遠維持），"
            "結論取決於 PEG 目標與成長假設"
        ),
        is_applicable=applicable,
    )


def _discounted_cash_flow_per_share(
    cash_flow_per_share: float, start_growth: float, assumptions: ValuationAssumptions
) -> float:
    """Growth fades in a straight line from `start_growth` (year 1) to the terminal rate
    (final year); the value after that is a Gordon perpetuity."""
    rate, terminal = assumptions.required_return, assumptions.terminal_growth
    cash_flow, present_value = cash_flow_per_share, 0.0
    for year in range(1, DCF_YEARS + 1):
        fade = (year - 1) / (DCF_YEARS - 1)
        cash_flow *= 1 + start_growth + (terminal - start_growth) * fade
        present_value += cash_flow / (1 + rate) ** year
    terminal_value = cash_flow * (1 + terminal) / (rate - terminal)
    return present_value + terminal_value / (1 + rate) ** DCF_YEARS


def _reports_in_trading_currency(fundamentals: Fundamentals) -> bool:
    """An ADR can trade in dollars while reporting in another currency (TSM: USD price,
    TWD statements). Cash flow per share would then be off by the exchange rate, so the
    model stays out unless the two currencies are known to match or one is unstated."""
    trading, reporting = fundamentals.text("currency"), fundamentals.text("financialCurrency")
    return not trading or not reporting or trading.upper() == reporting.upper()


def dcf_value(
    fundamentals: Fundamentals, assumptions: ValuationAssumptions
) -> ValuationEstimate:
    free_cash_flow = fundamentals.number("freeCashflow") or 0.0
    shares = fundamentals.number("sharesOutstanding") or 0.0
    terminal = assumptions.terminal_growth
    eligible = (
        free_cash_flow > 0
        and shares > 0
        and assumptions.required_return > terminal
        and fundamentals.text("sector") not in DCF_EXCLUDED_SECTORS
        and _reports_in_trading_currency(fundamentals)
    )
    growth = implied_earnings_growth(fundamentals)
    start_growth = min(max(growth, terminal), assumptions.growth_cap) if growth else terminal
    value = (
        _discounted_cash_flow_per_share(free_cash_flow / shares, start_growth, assumptions)
        if eligible
        else None
    )
    return ValuationEstimate(
        model_name="DCF（現金流折現）",
        fair_value=value,
        assumptions={
            "freeCashFlowPerShare": free_cash_flow / shares if shares else 0.0,
            "startGrowth": start_growth,
            "terminalGrowth": terminal,
            "discountRate": assumptions.required_return,
        },
        note=(
            f"{DCF_YEARS} 年成長逐年淡出到終值成長 {terminal:.0%}；"
            "自由現金流取自財報（未扣股票薪酬、未加淨現金），終值佔比大、對假設極敏感；"
            "金融／不動產業、財報幣別與交易幣別不同者不適用"
        ),
        is_applicable=eligible,
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
        peg_value(fundamentals, assumptions),
        dcf_value(fundamentals, assumptions),
        gordon_growth_value(fundamentals, assumptions),
        analyst_consensus_value(fundamentals),
    ]
    return ValuationReport(
        symbol=symbol,
        last_price=last_price,
        estimates=estimates,
        earnings_growth=implied_earnings_growth(fundamentals),
    )
