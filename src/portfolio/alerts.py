from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .market import Quote

@dataclass(frozen=True)
class AlertRule:
    symbol: str
    buy_below: float | None = None
    sell_above: float | None = None
    last_fired_date: str | None = None

    @property
    def has_target(self) -> bool:
        return self.buy_below is not None or self.sell_above is not None

    def already_fired_on(self, today: date) -> bool:
        return self.last_fired_date == today.isoformat()


@dataclass(frozen=True)
class TriggeredAlert:
    symbol: str
    direction: str
    last_price: float
    target_price: float

    @property
    def gap_percent(self) -> float:
        if not self.target_price:
            return 0.0
        return (self.last_price / self.target_price - 1) * 100

    @property
    def headline(self) -> str:
        verb = "跌破入手價" if self.direction == "BUY" else "突破出場價"
        gap = "低於" if self.gap_percent < 0 else "高於" if self.gap_percent > 0 else "等於"
        return (
            f"{self.symbol} {verb} ${self.target_price:,.2f}（現價 ${self.last_price:,.2f}）"
            f"｜{gap}目標價 {abs(self.gap_percent):.1f}%"
        )


@dataclass(frozen=True)
class AlertStatus:
    """A rule plus where the price currently sits, for the watchlist view."""

    rule: AlertRule
    last_price: float

    @property
    def distance_to_buy_percent(self) -> float | None:
        if self.rule.buy_below is None or not self.rule.buy_below:
            return None
        return (self.last_price / self.rule.buy_below - 1) * 100

    @property
    def is_buy_triggered(self) -> bool:
        return self.rule.buy_below is not None and self.last_price <= self.rule.buy_below

    @property
    def is_sell_triggered(self) -> bool:
        return self.rule.sell_above is not None and self.last_price >= self.rule.sell_above


def _triggered_for(rule: AlertRule, quote: Quote) -> TriggeredAlert | None:
    if rule.buy_below is not None and quote.last_price <= rule.buy_below:
        return TriggeredAlert(rule.symbol, "BUY", quote.last_price, rule.buy_below)
    if rule.sell_above is not None and quote.last_price >= rule.sell_above:
        return TriggeredAlert(rule.symbol, "SELL", quote.last_price, rule.sell_above)
    return None


def evaluate_rules(
    rules: list[AlertRule], quotes: dict[str, Quote], today: date, resend_same_day: bool = False
) -> list[TriggeredAlert]:
    triggered = []
    for rule in rules:
        quote = quotes.get(rule.symbol)
        if quote is None or (rule.already_fired_on(today) and not resend_same_day):
            continue
        alert = _triggered_for(rule, quote)
        if alert is not None:
            triggered.append(alert)
    return triggered


def build_statuses(rules: list[AlertRule], quotes: dict[str, Quote]) -> list[AlertStatus]:
    statuses = [
        AlertStatus(rule=rule, last_price=quotes[rule.symbol].last_price)
        for rule in rules
        if rule.symbol in quotes
    ]
    return sorted(statuses, key=lambda status: status.distance_to_buy_percent or 1e9)
