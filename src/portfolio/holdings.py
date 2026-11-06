from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .firstrade_csv import BrokerageTransaction

SHARE_EPSILON = 1e-6


@dataclass(frozen=True)
class TradeExecution:
    """One buy or sell, including the FX rate that applied on its trade date."""

    shares: float
    price: float
    charges: float
    trade_date: date
    fx_rate: float | None = None

    @property
    def gross_cost(self) -> float:
        return self.shares * self.price + self.charges

    @property
    def gross_proceeds(self) -> float:
        return self.shares * self.price - self.charges


@dataclass
class Position:
    """Running cost basis for one symbol under the moving-average method."""

    symbol: str
    quantity: float = 0.0
    total_cost: float = 0.0
    total_cost_home: float = 0.0
    realized_gain: float = 0.0
    first_trade_date: date | None = None
    last_trade_date: date | None = None
    unpriced_quantity: float = 0.0
    """Shares proven held by a dividend but bought before the import window, so their
    purchase price is unknown. Kept apart from quantity so cost maths stays honest."""

    @property
    def average_cost_rate(self) -> float | None:
        """Weighted FX rate actually paid; None until any trade carries a rate."""
        if not self.total_cost or not self.total_cost_home:
            return None
        return self.total_cost_home / self.total_cost

    @property
    def average_cost(self) -> float:
        if abs(self.quantity) < SHARE_EPSILON:
            return 0.0
        return self.total_cost / self.quantity

    @property
    def is_open(self) -> bool:
        return abs(self.quantity) >= SHARE_EPSILON

    def _record_trade_date(self, trade_date: date) -> None:
        if self.first_trade_date is None:
            self.first_trade_date = trade_date
        self.last_trade_date = trade_date

    def apply_purchase(self, execution: TradeExecution) -> None:
        self.quantity += execution.shares
        self.total_cost += execution.gross_cost
        if execution.fx_rate:
            self.total_cost_home += execution.gross_cost * execution.fx_rate
        self._record_trade_date(execution.trade_date)

    def apply_sale(self, execution: TradeExecution) -> None:
        sold = min(execution.shares, self.quantity) if self.quantity > 0 else execution.shares
        released = self.average_cost * sold
        self.realized_gain += sold * execution.price - execution.charges - released
        self._release_home_cost(released)
        self.quantity -= sold
        self.total_cost -= released
        self._record_trade_date(execution.trade_date)

    def _release_home_cost(self, released_base: float) -> None:
        """Releases home-currency cost in the same proportion, keeping the paid rate intact."""
        if not self.total_cost or not self.total_cost_home:
            return
        self.total_cost_home -= self.total_cost_home * (released_base / self.total_cost)


@dataclass
class PositionLedger:
    positions: dict[str, Position] = field(default_factory=dict)

    def _position_for(self, symbol: str) -> Position:
        return self.positions.setdefault(symbol, Position(symbol=symbol))

    def apply(self, transaction: BrokerageTransaction) -> None:
        if not transaction.symbol or transaction.activity not in {"BUY", "SELL"}:
            return
        position = self._position_for(transaction.symbol)
        execution = _to_execution(transaction)
        if transaction.activity == "BUY":
            position.apply_purchase(execution)
        else:
            position.apply_sale(execution)

    def open_positions(self) -> list[Position]:
        open_only = [position for position in self.positions.values() if position.is_open]
        return sorted(open_only, key=lambda position: position.symbol)

    def total_realized_gain(self) -> float:
        return sum(position.realized_gain for position in self.positions.values())


@dataclass(frozen=True)
class ManualPosition:
    """A holding the broker export cannot explain, entered by hand."""

    symbol: str
    quantity: float
    average_cost: float
    note: str = ""

    @property
    def total_cost(self) -> float:
        return self.quantity * self.average_cost

    def as_position(self) -> Position:
        return Position(
            symbol=self.symbol.upper(), quantity=self.quantity, total_cost=self.total_cost
        )


def _to_execution(transaction: BrokerageTransaction) -> TradeExecution:
    return TradeExecution(
        shares=abs(transaction.quantity),
        price=abs(transaction.price) or _implied_price(transaction),
        charges=abs(transaction.commission) + abs(transaction.fee),
        trade_date=transaction.trade_date,
        fx_rate=transaction.fx_rate,
    )


def _implied_price(transaction: BrokerageTransaction) -> float:
    """Fall back to cash/shares when a broker row omits the unit price."""
    shares = abs(transaction.quantity)
    if shares < SHARE_EPSILON:
        return 0.0
    return abs(transaction.cash_amount) / shares


def build_ledger(transactions: list[BrokerageTransaction]) -> PositionLedger:
    ledger = PositionLedger()
    for transaction in sorted(transactions, key=lambda item: item.trade_date):
        ledger.apply(transaction)
    return ledger
