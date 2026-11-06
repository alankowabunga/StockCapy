import pytest
from fastapi.testclient import TestClient

from conftest import make_settings
from portfolio.repository import save_alert_rule
from portfolio.service import (
    DispatchOptions,
    SignalScope,
    load_signal_subscriptions,
    run_signal_check,
    set_signal_subscription,
)
from portfolio.signal_subscriptions import (
    DefaultScope,
    InvalidSymbolError,
    SignalSubscription,
    load_subscription_overrides,
    normalize_symbol,
    resolve_subscriptions,
    store_subscription_choice,
)
from portfolio.store import open_store
from test_signal_service import FakeClient

SCOPE = DefaultScope(held=frozenset({"NVDA", "VTI"}), watched=frozenset({"VTI", "TSM"}))


def test_holdings_and_watchlist_are_in_by_default_with_their_sources():
    resolved = resolve_subscriptions(SCOPE, {})
    assert resolved == [
        SignalSubscription("NVDA", ("持股",), True),
        SignalSubscription("TSM", ("監控",), True),
        SignalSubscription("VTI", ("持股", "監控"), True),
    ]


def test_overrides_mute_a_default_symbol_and_follow_a_new_one():
    resolved = {s.symbol: s for s in resolve_subscriptions(SCOPE, {"VTI": False, "AAPL": True})}
    assert resolved["VTI"].is_enabled is False
    assert resolved["AAPL"] == SignalSubscription("AAPL", ("手動加入",), True)


@pytest.mark.parametrize(
    ("symbol", "enabled", "stored"),
    [("VTI", False, {"VTI": False}), ("VTI", True, {}), ("AAPL", True, {"AAPL": True}), ("AAPL", False, {})],
)
def test_only_real_exceptions_are_stored(tmp_path, symbol, enabled, stored):
    with open_store(make_settings(tmp_path).database_path) as connection:
        store_subscription_choice(connection, SCOPE, symbol, not enabled)
        store_subscription_choice(connection, SCOPE, symbol, enabled)
        assert load_subscription_overrides(connection) == stored


@pytest.mark.parametrize("raw", ["", "  ", "NV DA", "<script>", "A" * 16])
def test_invalid_symbols_are_rejected(raw):
    with pytest.raises(InvalidSymbolError):
        normalize_symbol(raw)


def test_symbols_are_normalized():
    assert normalize_symbol(" brk-b ") == "BRK-B"


@pytest.fixture
def delivering(monkeypatch):
    from portfolio.notifiers import DeliveryResult

    monkeypatch.setattr(
        "portfolio.service.dispatch_signals",
        lambda settings, signals: [DeliveryResult("stub", True)] if signals else [],
    )


def test_muted_symbol_is_listed_but_not_pushed(tmp_path, delivering):
    settings = make_settings(tmp_path)
    with open_store(settings.database_path) as connection:
        save_alert_rule(connection, "NVDA", 70.0, None)
    set_signal_subscription(settings, "nvda", enabled=False)
    outcome = run_signal_check(settings, FakeClient())
    assert [signal.symbol for signal in outcome.signals] == ["NVDA"]
    assert outcome.signals[0].is_muted
    assert outcome.fresh == []


def test_followed_symbol_is_scanned_without_holding_or_watching_it(tmp_path):
    settings = make_settings(tmp_path)
    assert run_signal_check(settings, FakeClient(), options=DispatchOptions(notify=False)).signals == []
    result = set_signal_subscription(settings, "NVDA", enabled=True)
    assert result == SignalSubscription("NVDA", ("手動加入",), True)
    outcome = run_signal_check(settings, FakeClient(), options=DispatchOptions(notify=False))
    assert [signal.symbol for signal in outcome.fresh] == ["NVDA"]


def test_unfollowing_removes_the_symbol_entirely(tmp_path):
    settings = make_settings(tmp_path)
    set_signal_subscription(settings, "NVDA", enabled=True)
    result = set_signal_subscription(settings, "NVDA", enabled=False)
    assert result.sources == ()
    assert load_signal_subscriptions(settings) == []


def test_explicit_scope_still_marks_muted_symbols(tmp_path):
    settings = make_settings(tmp_path)
    with open_store(settings.database_path) as connection:
        save_alert_rule(connection, "NVDA", 70.0, None)
    set_signal_subscription(settings, "NVDA", enabled=False)
    outcome = run_signal_check(
        settings, FakeClient(), SignalScope(symbols=["NVDA"]), DispatchOptions(notify=False)
    )
    assert outcome.signals[0].is_muted and outcome.fresh == []


def test_api_lists_and_toggles_subscriptions(tmp_path, monkeypatch):
    from portfolio.web.api import create_app
    from test_api import FakeMarketDataClient

    monkeypatch.setattr("portfolio.web.api.MarketDataClient", FakeMarketDataClient)
    client = TestClient(create_app(make_settings(tmp_path)))
    assert client.get("/api/signals/subscriptions").json() == []
    added = client.put("/api/signals/subscriptions/tsm", json={"enabled": True})
    assert added.json() == {"symbol": "TSM", "sources": ["手動加入"], "enabled": True}
    assert client.get("/api/signals/subscriptions").json() == [added.json()]
    assert client.put("/api/signals/subscriptions/bad%20symbol", json={"enabled": True}).status_code == 400
