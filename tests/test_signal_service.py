import dataclasses

import pandas as pd
import pytest

from conftest import make_settings
from portfolio.market import Quote
from portfolio.notifiers import DeliveryResult
from portfolio.service import (
    DispatchOptions,
    SignalScope,
    evaluate_signals,
    load_signal_history,
    market_context,
    run_signal_check,
)
from portfolio.signal_config import ThresholdOverride, save_threshold_override
from portfolio.store import open_store

# NVDA sits ~20% below its 60-day line; SPY is calm, so the drop is stock-specific.
# Date-indexed like the real close_prices, so the price-history cache round-trips.
# Ends today: the engine now refuses stale closes, so a fixed start date would make
# these fixtures expire as the calendar moves.
_INDEX = pd.date_range(end=pd.Timestamp.today().normalize(), periods=60, freq="D")
NVDA_CLOSES = pd.Series([100.0] * 59 + [80.0], index=_INDEX)
SPY_CLOSES = pd.Series([500.0] * 60, index=_INDEX)


class FakeClient:
    def __init__(self, spy=SPY_CLOSES):
        self.quote_calls = 0
        self.close_calls: dict[str, int] = {}
        self._spy = spy

    def close_prices(self, symbol, start, end):
        self.close_calls[symbol] = self.close_calls.get(symbol, 0) + 1
        return self._spy if symbol == "SPY" else NVDA_CLOSES

    def quote(self, symbol):
        self.quote_calls += 1
        return Quote(symbol=symbol, last_price=80.0, previous_close=100.0, currency="USD")



@pytest.fixture
def delivering(monkeypatch):
    """Suppression now requires a real delivery, so tests that want the 'already sent
    today' state must pretend a channel accepted the message."""
    monkeypatch.setattr(
        "portfolio.service.dispatch_signals",
        lambda settings, signals: [DeliveryResult("stub", True)] if signals else [],
    )


@pytest.fixture
def failing_delivery(monkeypatch):
    monkeypatch.setattr(
        "portfolio.service.dispatch_signals",
        lambda settings, signals: [DeliveryResult("stub", False, "channel down")],
    )


def test_evaluate_signals_returns_a_buy_for_the_dropped_symbol(tmp_path):
    settings = make_settings(tmp_path)
    signals = evaluate_signals(settings, FakeClient(), scope=SignalScope(symbols=["NVDA"]))
    assert len(signals) == 1
    assert signals[0].symbol == "NVDA"
    assert signals[0].direction == "BUY"
    assert not signals[0].likely_market_wide  # SPY was flat


def test_market_context_reads_the_benchmark_move(tmp_path):
    settings = make_settings(tmp_path)
    context = market_context(settings, FakeClient())
    assert context.benchmark_symbol == "SPY"
    assert context.change_percent == 0.0  # flat benchmark


def test_a_signal_is_not_re_sent_the_same_day(tmp_path, delivering):
    settings = make_settings(tmp_path)
    client = FakeClient()
    first = run_signal_check(
        settings, client, SignalScope(symbols=["NVDA"]), DispatchOptions(notify=True)
    )
    assert len(first.fresh) == 1
    second = run_signal_check(
        settings, client, SignalScope(symbols=["NVDA"]), DispatchOptions(notify=True)
    )
    assert second.signals  # still triggering
    assert second.fresh == []  # but already sent today


def test_resend_overrides_same_day_suppression(tmp_path, delivering):
    settings = make_settings(tmp_path)
    client = FakeClient()
    run_signal_check(
        settings, client, SignalScope(symbols=["NVDA"]), DispatchOptions(notify=True)
    )
    again = run_signal_check(
        settings,
        client,
        SignalScope(symbols=["NVDA"]),
        DispatchOptions(notify=True, resend=True),
    )
    assert len(again.fresh) == 1


def test_no_symbols_means_no_signals(tmp_path):
    # Empty database: no holdings, no watchlist -> nothing to evaluate.
    settings = make_settings(tmp_path)
    assert evaluate_signals(settings, FakeClient()) == []


def test_history_is_recorded_when_a_signal_fires(tmp_path, delivering):
    settings = make_settings(tmp_path)
    run_signal_check(
        settings, FakeClient(), SignalScope(symbols=["NVDA"]), DispatchOptions(notify=True)
    )
    history = load_signal_history(settings)
    assert len(history) == 1
    assert history[0].symbol == "NVDA"
    assert history[0].direction == "BUY"
    assert history[0].last_price == 80.0


def test_prices_are_fetched_once_per_day_then_served_from_cache(tmp_path):
    settings = make_settings(tmp_path)
    client = FakeClient()
    evaluate_signals(settings, client, scope=SignalScope(symbols=["NVDA"]))
    assert client.close_calls == {"NVDA": 1, "SPY": 1}
    evaluate_signals(settings, client, scope=SignalScope(symbols=["NVDA"]))
    # Second run the same day hits the cache; no new network fetches.
    assert client.close_calls == {"NVDA": 1, "SPY": 1}


def test_market_wide_signals_can_be_muted_from_notification(tmp_path):
    broad_selloff = pd.Series([500.0] * 55 + [460.0] * 5, index=_INDEX)  # SPY -8% recently
    muted = dataclasses.replace(make_settings(tmp_path), mute_market_wide_signals=True)
    outcome = run_signal_check(
        muted,
        FakeClient(spy=broad_selloff),
        SignalScope(symbols=["NVDA"]),
        DispatchOptions(notify=False),
    )
    assert len(outcome.signals) == 1  # still surfaced in the list
    assert outcome.signals[0].likely_market_wide
    assert outcome.fresh == []  # but not notified


def test_market_wide_signals_notify_when_muting_is_off(tmp_path):
    broad_selloff = pd.Series([500.0] * 55 + [460.0] * 5, index=_INDEX)
    settings = make_settings(tmp_path)  # mute off by default
    outcome = run_signal_check(
        settings,
        FakeClient(spy=broad_selloff),
        SignalScope(symbols=["NVDA"]),
        DispatchOptions(notify=False),
    )
    assert len(outcome.fresh) == 1


def test_a_failed_delivery_is_retried_on_the_next_check(tmp_path, failing_delivery):
    # Telegram being down must not consume the day's alert: before the fix the signal
    # was marked sent regardless of delivery, so the 30-minute tick skipped it forever.
    settings = make_settings(tmp_path)
    client = FakeClient()
    first = run_signal_check(
        settings, client, SignalScope(symbols=["NVDA"]), DispatchOptions(notify=True)
    )
    assert len(first.fresh) == 1
    assert not first.notified
    assert first.failed_channels == ["stub"]
    retry = run_signal_check(
        settings, client, SignalScope(symbols=["NVDA"]), DispatchOptions(notify=True)
    )
    assert len(retry.fresh) == 1  # still offered, not swallowed
    assert load_signal_history(settings) == []  # nothing was ever delivered


def test_a_dry_run_does_not_consume_the_days_notification(tmp_path, delivering):
    settings = make_settings(tmp_path)
    client = FakeClient()
    dry_run = run_signal_check(
        settings, client, SignalScope(symbols=["NVDA"]), DispatchOptions(notify=False)
    )
    assert len(dry_run.fresh) == 1
    assert not dry_run.notify_requested
    real = run_signal_check(
        settings, client, SignalScope(symbols=["NVDA"]), DispatchOptions(notify=True)
    )
    assert len(real.fresh) == 1  # the dry run left the state untouched
    assert real.notified


def test_no_configured_channel_leaves_the_signal_pending(tmp_path):
    # make_settings has no desktop/telegram/email channel, so dispatch returns nothing.
    settings = make_settings(tmp_path)
    client = FakeClient()
    run_signal_check(settings, client, SignalScope(symbols=["NVDA"]), DispatchOptions(notify=True))
    again = run_signal_check(
        settings, client, SignalScope(symbols=["NVDA"]), DispatchOptions(notify=True)
    )
    assert len(again.fresh) == 1


def test_stored_thresholds_change_what_triggers(tmp_path):
    settings = make_settings(tmp_path)
    client = FakeClient()
    before = {c.name for c in evaluate_signals(settings, client, scope=SignalScope(symbols=["NVDA"]))[0].conditions}
    assert "BELOW_SHORT_TREND" in before  # NVDA is ~20% below its 60-day line by default
    # Raise the trend-bias threshold above 20%: that one condition stops firing.
    with open_store(settings.database_path) as connection:
        save_threshold_override(connection, ThresholdOverride("NVDA", short_trend_bias_percent=25.0))
    after = {c.name for c in evaluate_signals(settings, client, scope=SignalScope(symbols=["NVDA"]))[0].conditions}
    assert "BELOW_SHORT_TREND" not in after


def test_quote_failure_exposes_dated_fallback(tmp_path):
    class UnavailableQuote(FakeClient):
        def quote(self, symbol):
            raise OSError("offline")

    from portfolio.advisor import narrate_signal
    signal = evaluate_signals(make_settings(tmp_path), UnavailableQuote(), SignalScope(symbols=["NVDA"]))[0]
    assert signal.price_source == "adjusted_close"
    assert signal.snapshot.as_of == _INDEX[-1].date().isoformat()
    assert signal.retrieved_at is None
    assert "非即時報價" in narrate_signal(signal)


def test_stored_threshold_and_quote_metadata_reach_the_message(tmp_path):
    from portfolio.advisor import narrate_signal
    settings = make_settings(tmp_path)
    with open_store(settings.database_path) as connection:
        save_threshold_override(connection, ThresholdOverride("NVDA", rsi_oversold=22.0))
    signal = evaluate_signals(settings, FakeClient(), SignalScope(symbols=["NVDA"]))[0]
    assert signal.price_source == "quote"
    assert signal.retrieved_at.endswith("UTC")
    assert "低於設定門檻 22" in narrate_signal(signal)


def test_short_benchmark_history_reports_actual_interval(tmp_path):
    spy = pd.Series([500.0, float("nan"), 480.0, 460.0], index=_INDEX[:4])
    context = market_context(make_settings(tmp_path), FakeClient(spy=spy))
    assert context.sessions == 2
    assert context.start_date == _INDEX[0].date().isoformat()
    assert context.end_date == _INDEX[3].date().isoformat()
    assert context.change_percent == pytest.approx(-8)


def test_notification_explains_added_removed_and_unchanged_rules(tmp_path, delivering):
    settings = make_settings(tmp_path)
    scope = SignalScope(symbols=["NVDA"])
    first = run_signal_check(settings, FakeClient(), scope)
    assert "首次提醒" in first.fresh[0].notification_note
    with open_store(settings.database_path) as connection:
        save_threshold_override(connection, ThresholdOverride("NVDA", short_trend_bias_percent=25.0))
    second = run_signal_check(settings, FakeClient(), scope)
    assert "本次不再觸發：低於 60 日均線" in second.fresh[0].notification_note
    with open_store(settings.database_path) as connection:
        save_threshold_override(connection, ThresholdOverride("NVDA", short_trend_bias_percent=10.0))
    third = run_signal_check(settings, FakeClient(), scope)
    assert "本次新增：低於 60 日均線" in third.fresh[0].notification_note
    resent = run_signal_check(settings, FakeClient(), scope, DispatchOptions(resend=True))
    assert resent.fresh[0].notification_note == "手動重送｜條件與上次已通知相同"


def test_unchanged_signal_is_held_back_until_price_moves_further(tmp_path, delivering):
    class LowerQuote(FakeClient):
        def quote(self, symbol):
            return Quote(symbol=symbol, last_price=75.0, previous_close=100.0, currency="USD")

    settings = make_settings(tmp_path)
    scope = SignalScope(symbols=["NVDA"])
    run_signal_check(settings, FakeClient(), scope)  # delivered at $80
    assert run_signal_check(settings, FakeClient(), scope).fresh == []
    deeper = run_signal_check(settings, LowerQuote(), scope)  # $75 is 6.25% further down
    assert len(deeper.fresh) == 1
    assert "再跌 6.2%" in deeper.fresh[0].notification_note
