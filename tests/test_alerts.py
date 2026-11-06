from datetime import date

import pytest

from portfolio.alerts import (
    AlertRule,
    AlertStatus,
    TriggeredAlert,
    build_statuses,
    evaluate_rules,
)
from portfolio.market import Quote

TODAY = date(2026, 9, 16)


def quote(symbol: str, price: float) -> Quote:
    return Quote(symbol=symbol, last_price=price, previous_close=price, currency="USD")


def test_buy_alert_fires_at_or_below_target():
    rules = [AlertRule("MSFT", buy_below=400.0)]
    triggered = evaluate_rules(rules, {"MSFT": quote("MSFT", 400.0)}, TODAY)
    assert triggered[0].direction == "BUY"
    assert triggered[0].target_price == pytest.approx(400.0)


def test_buy_alert_stays_quiet_above_target():
    rules = [AlertRule("MSFT", buy_below=400.0)]
    assert evaluate_rules(rules, {"MSFT": quote("MSFT", 400.01)}, TODAY) == []


def test_sell_alert_fires_at_or_above_target():
    rules = [AlertRule("NVDA", sell_above=300.0)]
    triggered = evaluate_rules(rules, {"NVDA": quote("NVDA", 305.0)}, TODAY)
    assert triggered[0].direction == "SELL"


def test_buy_target_wins_when_both_would_trigger():
    """A nonsensical rule must still behave deterministically."""
    rules = [AlertRule("X", buy_below=500.0, sell_above=100.0)]
    triggered = evaluate_rules(rules, {"X": quote("X", 300.0)}, TODAY)
    assert len(triggered) == 1
    assert triggered[0].direction == "BUY"


def test_alert_already_fired_today_is_suppressed():
    rules = [AlertRule("MSFT", buy_below=400.0, last_fired_date=TODAY.isoformat())]
    assert evaluate_rules(rules, {"MSFT": quote("MSFT", 380.0)}, TODAY) == []


def test_resend_flag_overrides_same_day_suppression():
    rules = [AlertRule("MSFT", buy_below=400.0, last_fired_date=TODAY.isoformat())]
    triggered = evaluate_rules(rules, {"MSFT": quote("MSFT", 380.0)}, TODAY, resend_same_day=True)
    assert len(triggered) == 1


def test_alert_fired_yesterday_fires_again_today():
    rules = [AlertRule("MSFT", buy_below=400.0, last_fired_date="2026-09-15")]
    assert len(evaluate_rules(rules, {"MSFT": quote("MSFT", 380.0)}, TODAY)) == 1


def test_missing_quote_does_not_crash():
    assert evaluate_rules([AlertRule("ZZZZ", buy_below=1.0)], {}, TODAY) == []


def test_rule_without_any_target_never_fires():
    rules = [AlertRule("MSFT")]
    assert not rules[0].has_target
    assert evaluate_rules(rules, {"MSFT": quote("MSFT", 1.0)}, TODAY) == []


def test_gap_percent_is_negative_once_the_target_is_reached():
    alert = TriggeredAlert("MSFT", "BUY", last_price=380.0, target_price=400.0)
    assert alert.gap_percent == pytest.approx(-5.0)
    assert "跌破入手價" in alert.headline


def test_statuses_are_sorted_by_closeness_to_the_buy_target():
    rules = [AlertRule("FAR", buy_below=100.0), AlertRule("NEAR", buy_below=99.0)]
    quotes = {"FAR": quote("FAR", 200.0), "NEAR": quote("NEAR", 100.0)}
    assert [status.rule.symbol for status in build_statuses(rules, quotes)] == ["NEAR", "FAR"]


def test_status_reports_trigger_state_and_distance():
    status = AlertStatus(rule=AlertRule("MSFT", buy_below=400.0), last_price=380.0)
    assert status.is_buy_triggered
    assert not status.is_sell_triggered
    assert status.distance_to_buy_percent == pytest.approx(-5.0)


def test_status_without_a_buy_target_has_no_distance():
    status = AlertStatus(rule=AlertRule("MSFT", sell_above=600.0), last_price=500.0)
    assert status.distance_to_buy_percent is None


def test_applescript_escaping_prevents_broken_notifications():
    from portfolio.notifiers import _escape_for_applescript

    assert _escape_for_applescript('say "hi"') == 'say \\"hi\\"'
    assert _escape_for_applescript("back\\slash") == "back\\\\slash"
