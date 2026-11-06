from datetime import date

import pytest

from portfolio.config import RenotifyPolicy
from portfolio.indicators import BollingerBands, IndicatorSnapshot
from portfolio.renotify import LastNotification, decide_renotification, signal_conditions_key
from portfolio.signals import BUY, SELL, Signal, SignalCondition

TODAY = date(2026, 11, 6)
POLICY = RenotifyPolicy(further_move_percent=5.0, reminder_days=7)
SNAPSHOT = IndicatorSnapshot(
    symbol="NVDA", last_close=100.0, rsi=40.0, sma_short=110.0, sma_long=120.0,
    bands=BollingerBands(lower=102.0, middle=110.0, upper=118.0),
)


def _signal(price: float, direction: str = BUY, conditions=(SignalCondition.BELOW_LOWER_BAND,)) -> Signal:
    return Signal("NVDA", direction, tuple(conditions), SNAPSHOT, last_price=price)


def _sent(days_ago: int, price: float | None, signal: Signal) -> LastNotification:
    fired_on = date.fromordinal(TODAY.toordinal() - days_ago)
    return LastNotification(fired_on, signal_conditions_key(signal), price)


def test_first_signal_is_always_sent():
    decision = decide_renotification(_signal(100.0), None, POLICY, TODAY)
    assert decision.should_notify
    assert "首次提醒" in decision.note


def test_unchanged_signal_next_day_is_held_back():
    signal = _signal(98.0)
    decision = decide_renotification(signal, _sent(1, 100.0, signal), POLICY, TODAY)
    assert not decision.should_notify


def test_changed_conditions_are_sent_with_the_difference():
    before = _signal(100.0)
    after = _signal(99.0, conditions=(SignalCondition.BELOW_LOWER_BAND, SignalCondition.OVERSOLD_RSI))
    decision = decide_renotification(after, _sent(1, 100.0, before), POLICY, TODAY)
    assert decision.should_notify
    assert "本次新增" in decision.note


@pytest.mark.parametrize(
    ("direction", "price", "expected"),
    [(BUY, 95.0, True), (BUY, 95.5, False), (BUY, 106.0, False), (SELL, 105.0, True), (SELL, 95.0, False)],
)
def test_further_move_in_the_signal_direction_re_arms(direction, price, expected):
    signal = _signal(price, direction=direction)
    decision = decide_renotification(signal, _sent(1, 100.0, signal), POLICY, TODAY)
    assert decision.should_notify is expected


def test_further_move_note_names_the_last_price():
    signal = _signal(90.0)
    decision = decide_renotification(signal, _sent(2, 100.0, signal), POLICY, TODAY)
    assert "再跌 10.0%" in decision.note
    assert "$100.00" in decision.note


@pytest.mark.parametrize(("days_ago", "expected"), [(6, False), (7, True)])
def test_a_quiet_spell_triggers_a_still_firing_reminder(days_ago, expected):
    signal = _signal(100.0)
    decision = decide_renotification(signal, _sent(days_ago, 100.0, signal), POLICY, TODAY)
    assert decision.should_notify is expected
    if expected:
        assert "已 7 天" in decision.note


def test_missing_last_price_skips_only_the_move_rule():
    signal = _signal(50.0)
    decision = decide_renotification(signal, _sent(1, None, signal), POLICY, TODAY)
    assert not decision.should_notify
