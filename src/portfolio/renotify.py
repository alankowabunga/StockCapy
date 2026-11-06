"""When an already-notified smart alert is worth sending again.

A condition that keeps firing (a stock sitting under its lower band for a week) must not
produce an identical message every day, but it also must not go silent forever: the
user would read the silence as "the signal went away"."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .config import RenotifyPolicy
from .signals import BUY, Signal, SignalCondition


UNCHANGED_NOTE = "條件與上次已通知相同"


@dataclass(frozen=True)
class LastNotification:
    fired_on: date
    conditions_key: str
    price: float | None


@dataclass(frozen=True)
class RenotifyDecision:
    should_notify: bool
    note: str


def signal_conditions_key(signal: Signal) -> str:
    names = ",".join(sorted(condition.name for condition in signal.conditions))
    return f"{signal.direction}:{names}"


def decide_renotification(
    signal: Signal, last: LastNotification | None, policy: RenotifyPolicy, today: date
) -> RenotifyDecision:
    if last is None:
        return RenotifyDecision(True, "首次提醒（尚無已通知紀錄）")
    if last.conditions_key != signal_conditions_key(signal):
        return RenotifyDecision(True, _conditions_changed_note(signal, last))
    further = _further_move_percent(signal, last)
    if further is not None and further >= policy.further_move_percent:
        return RenotifyDecision(True, _further_move_note(signal, last, further))
    days = (today - last.fired_on).days
    if days >= policy.reminder_days:
        return RenotifyDecision(True, f"條件持續觸發中（上次通知 {last.fired_on.isoformat()}，已 {days} 天）")
    return RenotifyDecision(False, UNCHANGED_NOTE)


def _further_move_percent(signal: Signal, last: LastNotification) -> float | None:
    """How far the price kept going the signal's way since the last message."""
    if not last.price:
        return None
    change = (signal.last_price / last.price - 1) * 100
    return -change if signal.direction == BUY else change


def _further_move_note(signal: Signal, last: LastNotification, further: float) -> str:
    verb = "再跌" if signal.direction == BUY else "再漲"
    return (
        f"條件與上次相同，但價格比上次通知（{last.fired_on.isoformat()}，"
        f"${last.price:,.2f}）{verb} {further:.1f}%"
    )


def _conditions_changed_note(signal: Signal, last: LastNotification) -> str:
    previous = set(last.conditions_key.partition(":")[2].split(","))
    current = {condition.name for condition in signal.conditions}
    changes = []
    added = [c.label for c in SignalCondition if c.name in current - previous]
    removed = [c.label for c in SignalCondition if c.name in previous - current]
    if added:
        changes.append("本次新增：" + "、".join(added))
    if removed:
        changes.append("本次不再觸發：" + "、".join(removed))
    if not changes:
        changes.append("買賣方向改變")
    return f"相較上次已通知（{last.fired_on.isoformat()}）：\n" + "\n".join(changes)
