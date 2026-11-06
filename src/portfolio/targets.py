from __future__ import annotations

from dataclasses import dataclass

from .entry_verdict import EntryVerdict, VerdictInputs, build_entry_verdict
from .holdings import Position
from .market import MarketDataClient, PricePosition
from .uncertainty import UncertaintyAssessment
from .valuation import ValuationAssumptions, ValuationReport, build_valuation_report

VALUATION_BASIS = "估值模型"
MOVING_AVERAGE_BASIS = "200日均線"
NO_BASIS = "無"
DATA_UNAVAILABLE_BASIS = "資料不足"


@dataclass(frozen=True)
class EntryTarget:
    """Everything needed to judge an entry price, with no recommendation attached."""

    symbol: str
    average_cost: float | None
    valuation: ValuationReport
    price_position: PricePosition
    assumptions: ValuationAssumptions
    trailing_pe: float | None = None
    assumption_source: str = "default"
    historical_median_pe: float | None = None
    fundamentals_available: bool = True

    @property
    def last_price(self) -> float:
        return self.valuation.last_price

    @property
    def model_count(self) -> int:
        return len(self.valuation.aggregated_values)

    @property
    def reference_price(self) -> float | None:
        """Intrinsic models when they apply; otherwise the fund's own trend line."""
        if self.model_count:
            return self.valuation.median_fair_value
        if self.price_position.is_fund and self.price_position.two_hundred_day_average:
            return self.price_position.two_hundred_day_average
        return None

    @property
    def basis(self) -> str:
        if self.model_count:
            return VALUATION_BASIS
        if self.price_position.is_fund and self.price_position.two_hundred_day_average:
            return MOVING_AVERAGE_BASIS
        if not self.fundamentals_available:
            return DATA_UNAVAILABLE_BASIS
        return NO_BASIS

    @property
    def uncertainty(self) -> UncertaintyAssessment | None:
        """None when the reference is not model-based (a fund's trend line, or nothing)."""
        return self.valuation.uncertainty() if self.model_count else None

    @property
    def margin_of_safety(self) -> float:
        """The margin actually applied: the user's, widened when the fair value is shaky."""
        if self.model_count:
            return self.valuation.effective_margin_of_safety(self.assumptions)
        return self.assumptions.margin_of_safety

    @property
    def buy_below(self) -> float | None:
        reference = self.reference_price
        if reference is None:
            return None
        return reference * (1 - self.margin_of_safety)

    @property
    def upside_percent(self) -> float | None:
        """Gap from the price to the reference price — how far the user's own estimate
        sits from what the market is asking."""
        reference = self.reference_price
        if reference is None or not self.last_price:
            return None
        return (reference / self.last_price - 1) * 100

    @property
    def verdict(self) -> EntryVerdict | None:
        return build_entry_verdict(
            VerdictInputs(
                upside_percent=self.upside_percent,
                margin_of_safety=self.margin_of_safety,
                model_count=self.model_count,
            )
        )

    @property
    def gap_to_buy_below_percent(self) -> float | None:
        target = self.buy_below
        if not target:
            return None
        return (self.last_price / target - 1) * 100

    @property
    def discount_to_average_cost_percent(self) -> float | None:
        if not self.average_cost:
            return None
        return (self.last_price / self.average_cost - 1) * 100

    @property
    def is_at_or_below_target(self) -> bool:
        target = self.buy_below
        return target is not None and self.last_price <= target


def build_entry_target(
    client: MarketDataClient,
    symbol: str,
    assumptions: ValuationAssumptions,
    average_cost: float | None = None,
) -> EntryTarget:
    quote = client.quote(symbol)
    fundamentals = client.fundamentals(symbol)
    return EntryTarget(
        symbol=symbol,
        average_cost=average_cost,
        valuation=build_valuation_report(symbol, quote.last_price, fundamentals, assumptions),
        price_position=client.price_position(symbol),
        assumptions=assumptions,
        trailing_pe=fundamentals.number("trailingPE"),
        fundamentals_available=not fundamentals.is_empty,
    )


def _average_cost_by_symbol(positions: list[Position]) -> dict[str, float]:
    return {position.symbol: position.average_cost for position in positions}


def build_entry_targets(
    client: MarketDataClient,
    assumptions_by_symbol: dict[str, ValuationAssumptions],
    positions: list[Position] | None = None,
) -> list[EntryTarget]:
    """Each symbol carries its own assumptions, so one bad default cannot skew them all."""
    average_costs = _average_cost_by_symbol(positions or [])
    targets = [
        build_entry_target(client, symbol, assumptions, average_costs.get(symbol))
        for symbol, assumptions in assumptions_by_symbol.items()
    ]
    return sorted(targets, key=lambda target: target.gap_to_buy_below_percent or 1e9)
