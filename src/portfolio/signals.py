from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

import pandas as pd

from .indicators import IndicatorSnapshot, compute_indicators

BUY = "BUY"
SELL = "SELL"


class SignalCondition(Enum):
    """A single rule that fired. The value is a short human label; the direction
    says which side of the market the condition points to.

    Nothing here is advice — each condition is a factual statement about where the
    price sits relative to a line the user chose.
    """

    BELOW_AVERAGE_COST = ("低於你的均價", BUY)
    BELOW_SHORT_TREND = ("低於 60 日均線", BUY)
    OVERSOLD_RSI = ("RSI 進入超賣區", BUY)
    BELOW_LOWER_BAND = ("跌破布林下軌", BUY)
    ABOVE_SHORT_TREND = ("高於 60 日均線", SELL)
    OVERBOUGHT_RSI = ("RSI 進入超買區", SELL)
    ABOVE_UPPER_BAND = ("突破布林上軌", SELL)

    def __init__(self, label: str, direction: str) -> None:
        self.label = label
        self.direction = direction


@dataclass(frozen=True)
class SignalThresholds:
    """The rules, written down. The doc's ranges collapse to single numbers here so
    the engine is deterministic; the user tunes them (see the backtester first)."""

    average_cost_discount_percent: float = 12.0  # trigger at or below -12% vs cost
    short_trend_bias_percent: float = 10.0  # trigger at or beyond ±10% vs 60-day line
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0


@dataclass(frozen=True)
class MarketContext:
    """The benchmark's own recent move, used only to label whether a drop is likely
    the whole market falling rather than this one stock (the doc's false-signal risk)."""

    benchmark_symbol: str
    change_percent: float
    broad_move_threshold_percent: float = 5.0
    sessions: int | None = None
    start_date: str | None = None
    end_date: str | None = None

    @property
    def is_broad_selloff(self) -> bool:
        return self.change_percent <= -self.broad_move_threshold_percent

    @property
    def is_broad_rally(self) -> bool:
        return self.change_percent >= self.broad_move_threshold_percent


@dataclass(frozen=True)
class ValuationContext:
    """What the reference price rests on, shown beside it so a lone number never reads
    as more certain than it is. Describes the estimate; says nothing about what to do."""

    model_names: tuple[str, ...]
    uncertainty_tier: str
    uncertainty_reasons: tuple[str, ...]
    margin_of_safety: float
    target_pe: float | None = None
    multiple_source: str = "default"

    @property
    def is_fragile(self) -> bool:
        return len(self.model_names) < 2


@dataclass(frozen=True)
class Signal:
    """A rule-triggered observation for one symbol. Not a recommendation."""

    symbol: str
    direction: str
    conditions: tuple[SignalCondition, ...]
    snapshot: IndicatorSnapshot
    last_price: float
    average_cost: float | None = None
    market_context: MarketContext | None = None
    thresholds: SignalThresholds = field(default_factory=SignalThresholds)
    price_source: str = "adjusted_close"
    retrieved_at: str | None = None
    currency: str = "USD"
    notification_note: str | None = None
    daily_change_percent: float | None = None
    reference_price: float | None = None
    buy_below_price: float | None = None
    valuation_basis: str | None = None
    valuation_context: ValuationContext | None = None
    is_muted: bool = False

    @property
    def severity(self) -> int:
        """How many independent rules agree. More is not 'more advice' — it is just
        how many of the user's own lines the price has crossed at once."""
        return len(self.conditions)

    @property
    def discount_to_average_cost_percent(self) -> float | None:
        if not self.average_cost:
            return None
        return (self.last_price / self.average_cost - 1) * 100

    @property
    def likely_market_wide(self) -> bool:
        """A buy signal during a broad sell-off (or a sell signal during a broad
        rally) is probably the market moving, not the stock."""
        context = self.market_context
        if context is None:
            return False
        if self.direction == BUY:
            return context.is_broad_selloff
        return context.is_broad_rally


@dataclass(frozen=True)
class SignalEventRecord:
    """A signal as it was recorded when it fired, for the history log. Kept flat (no
    live indicator objects) because it is a snapshot of the past, not a live reading."""

    event_date: str
    symbol: str
    direction: str
    conditions: str
    last_price: float


@dataclass(frozen=True)
class PositionPricing:
    """現價與我的成本語意成對（見 D13），分開傳兩個 float | None 容易叫錯順序。"""

    last_price: float | None = None
    average_cost: float | None = None
    retrieved_at: str | None = None
    currency: str = "USD"
    daily_change_percent: float | None = None

    def discount_percent_at(self, price: float) -> float | None:
        return (price / self.average_cost - 1) * 100 if self.average_cost else None


# A rule says where the price sits *today*. Indicators built from closes older than
# this describe some earlier week, whatever they claim — a long weekend plus a holiday
# is the most a healthy feed should ever be behind.
MAX_SIGNAL_DATA_AGE_DAYS = 5


@dataclass(frozen=True)
class StaleSignalData:
    """A symbol whose cached closes are too old to say anything about now."""

    symbol: str
    as_of: str | None
    age_days: int | None

    @property
    def describe_age(self) -> str:
        if self.as_of is None:
            return "沒有資料日期"
        return f"資料停在 {self.as_of}（{self.age_days} 天前）"


def signal_data_age_days(as_of: str | None, today: date) -> int | None:
    if not as_of:
        return None
    return (today - date.fromisoformat(as_of)).days


def is_signal_data_stale(as_of: str | None, today: date) -> bool:
    """No date is treated as stale: freshness that cannot be verified is not freshness."""
    age = signal_data_age_days(as_of, today)
    return age is None or age > MAX_SIGNAL_DATA_AGE_DAYS


@dataclass(frozen=True)
class PriceHistory:
    """symbol 與 closes 必須一起走，否則會把某檔的價格算到另一檔頭上。"""

    symbol: str
    closes: pd.Series


@dataclass(frozen=True)
class SignalEvent:
    """要寫進歷史紀錄的一次觸發。五個欄位一起走才是一筆完整紀錄。"""

    event_date: date
    symbol: str
    direction: str
    conditions: str
    last_price: float


def _buy_conditions(
    snapshot: IndicatorSnapshot,
    thresholds: SignalThresholds,
    discount_to_cost: float | None,
) -> list[SignalCondition]:
    conditions: list[SignalCondition] = []
    if discount_to_cost is not None and discount_to_cost <= -thresholds.average_cost_discount_percent:
        conditions.append(SignalCondition.BELOW_AVERAGE_COST)
    bias = snapshot.bias_to_sma_short_percent
    if bias is not None and bias <= -thresholds.short_trend_bias_percent:
        conditions.append(SignalCondition.BELOW_SHORT_TREND)
    if snapshot.rsi is not None and snapshot.rsi < thresholds.rsi_oversold:
        conditions.append(SignalCondition.OVERSOLD_RSI)
    if snapshot.below_lower_band:
        conditions.append(SignalCondition.BELOW_LOWER_BAND)
    return conditions


def _sell_conditions(
    snapshot: IndicatorSnapshot, thresholds: SignalThresholds
) -> list[SignalCondition]:
    conditions: list[SignalCondition] = []
    bias = snapshot.bias_to_sma_short_percent
    if bias is not None and bias >= thresholds.short_trend_bias_percent:
        conditions.append(SignalCondition.ABOVE_SHORT_TREND)
    if snapshot.rsi is not None and snapshot.rsi > thresholds.rsi_overbought:
        conditions.append(SignalCondition.OVERBOUGHT_RSI)
    if snapshot.above_upper_band:
        conditions.append(SignalCondition.ABOVE_UPPER_BAND)
    return conditions


def _triggered_side(
    snapshot: IndicatorSnapshot,
    thresholds: SignalThresholds,
    discount_to_cost: float | None,
) -> tuple[str, list[SignalCondition]] | None:
    """A stock cannot be both cheap and expensive; if both sides somehow fire, BUY wins,
    matching the price-alert rule."""
    buy = _buy_conditions(snapshot, thresholds, discount_to_cost)
    if buy:
        return BUY, buy
    sell = _sell_conditions(snapshot, thresholds)
    return (SELL, sell) if sell else None


def _nominal_price(snapshot: IndicatorSnapshot, pricing: PositionPricing) -> float:
    """The average-cost rule compares nominal prices while the snapshot is built from
    adjusted closes (see D13); without a live quote the adjusted close stands in."""
    return snapshot.last_close if pricing.last_price is None else pricing.last_price


def evaluate_signal(
    snapshot: IndicatorSnapshot,
    *,
    thresholds: SignalThresholds | None = None,
    pricing: PositionPricing | None = None,
    market_context: MarketContext | None = None,
) -> Signal | None:
    """Returns the triggered signal for one symbol, or None when no rule fired."""
    pricing = pricing or PositionPricing()
    price = _nominal_price(snapshot, pricing)
    side = _triggered_side(
        snapshot, thresholds or SignalThresholds(), pricing.discount_percent_at(price)
    )
    if side is None:
        return None
    direction, conditions = side
    return Signal(
        symbol=snapshot.symbol,
        direction=direction,
        conditions=tuple(conditions),
        snapshot=snapshot,
        last_price=price,
        average_cost=pricing.average_cost,
        market_context=market_context,
        thresholds=thresholds or SignalThresholds(),
        price_source="quote" if pricing.last_price is not None else "adjusted_close",
        retrieved_at=pricing.retrieved_at,
        currency=pricing.currency,
        daily_change_percent=pricing.daily_change_percent,
    )


def evaluate_signal_from_closes(
    history: PriceHistory,
    *,
    thresholds: SignalThresholds | None = None,
    pricing: PositionPricing | None = None,
    market_context: MarketContext | None = None,
) -> Signal | None:
    """Convenience wrapper for callers that hold a raw close series (backtester, tests)."""
    snapshot = compute_indicators(history.symbol, history.closes)
    if snapshot is None:
        return None
    return evaluate_signal(
        snapshot,
        thresholds=thresholds,
        pricing=pricing,
        market_context=market_context,
    )
