"""A4/C1: the alert and the full 入手價 view must resolve the same assumptions, so one
symbol can never show two different fair values; and a flaky lookup must not lose the alert."""
import pandas as pd
import pytest
from conftest import make_settings
from test_signal_service import SIGNAL_WINDOW_DAYS, FakeClient

from portfolio.advisor import narrate_signal
from portfolio.assumptions import AssumptionOverride, resolve_assumptions, save_override
from portfolio.market import Fundamentals, PricePosition
from portfolio.service import SignalScope, evaluate_signals
from portfolio.store import open_store
from portfolio.targets import build_entry_target

# 25% expected growth, rich P/E, no dividend: Graham and DDM out; Target P/E and PEG in.
GROWTH = Fundamentals(
    symbol="NVDA",
    values={"trailingEps": 4.0, "forwardEps": 5.0, "bookValue": 10.0, "trailingPE": 40.0,
            "sector": "Technology", "quoteType": "EQUITY"},
)
FLAT_EPS = {pd.Timestamp("2020-12-31"): 4.0}  # constant price / constant EPS -> P/E 20, stable


def _long_history() -> pd.Series:
    index = pd.date_range(end=pd.Timestamp.today().normalize(), periods=1300, freq="D")
    return pd.Series(80.0, index=index)


class ValuedClient(FakeClient):
    def __init__(self, annual_eps=None, eps_error=None):
        super().__init__()
        self._annual_eps = annual_eps if annual_eps is not None else {}
        self._eps_error = eps_error
        self.eps_calls = 0

    def fundamentals(self, symbol):
        return GROWTH

    def price_position(self, symbol):
        return PricePosition(symbol, 80.0, 60.0, 130.0, 100.0, "EQUITY")

    def annual_diluted_eps(self, symbol):
        self.eps_calls += 1
        if self._eps_error:
            raise self._eps_error
        return self._annual_eps

    def close_prices(self, symbol, start, end):
        if (end - start).days > SIGNAL_WINDOW_DAYS:
            self.history_calls += 1
            return _long_history()
        return super().close_prices(symbol, start, end)


def _signal(settings, client):
    signals = evaluate_signals(settings, client, scope=SignalScope(symbols=["NVDA"]))
    assert len(signals) == 1
    return signals[0]


def test_a_stable_history_sets_the_multiple_in_the_alert_too(tmp_path):
    settings = make_settings(tmp_path)
    signal = _signal(settings, ValuedClient(annual_eps=FLAT_EPS))
    context = signal.valuation_context
    assert context.multiple_source == "historical"
    assert context.target_pe == pytest.approx(20.0)
    assert context.model_names == ("Target P/E", "PEG（成長調整）")
    # median of Target P/E 4.0 x 20 = 80 and PEG 5.0 x 20 = 100
    assert signal.reference_price == pytest.approx(90.0)


def test_the_alert_and_the_full_view_show_the_same_fair_value(tmp_path):
    """The point of A4: no second, lighter assumption set that disagrees."""
    settings = make_settings(tmp_path)
    client = ValuedClient(annual_eps=FLAT_EPS)
    signal = _signal(settings, client)
    with open_store(settings.database_path) as connection:
        entry = resolve_assumptions(connection, client, "NVDA")
    full_view = build_entry_target(client, "NVDA", entry.assumptions)
    assert signal.reference_price == pytest.approx(full_view.reference_price)
    assert signal.buy_below_price == pytest.approx(full_view.buy_below)


def test_without_usable_history_the_default_multiple_is_used_and_labelled(tmp_path):
    signal = _signal(make_settings(tmp_path), ValuedClient(annual_eps={}))
    context = signal.valuation_context
    assert context.multiple_source == "default"
    assert context.target_pe == pytest.approx(18.0)
    assert signal.reference_price == pytest.approx((4.0 * 18.0 + 100.0) / 2)


def test_a_failing_history_lookup_never_costs_the_alert_its_valuation(tmp_path):
    client = ValuedClient(eps_error=RuntimeError("yfinance is down"))
    signal = _signal(make_settings(tmp_path), client)
    assert signal.reference_price is not None
    assert signal.valuation_context.multiple_source == "default"


def test_a_stored_override_is_labelled_manual_in_the_alert(tmp_path):
    settings = make_settings(tmp_path)
    with open_store(settings.database_path) as connection:
        save_override(connection, AssumptionOverride("NVDA", target_pe=30.0))
    signal = _signal(settings, ValuedClient(annual_eps=FLAT_EPS))
    assert signal.valuation_context.multiple_source == "manual"
    assert signal.valuation_context.target_pe == pytest.approx(30.0)


def test_a_margin_the_user_stored_is_used_as_is_in_the_alert(tmp_path):
    settings = make_settings(tmp_path)
    with open_store(settings.database_path) as connection:
        save_override(connection, AssumptionOverride("NVDA", margin_of_safety=0.10))
    signal = _signal(settings, ValuedClient(annual_eps=FLAT_EPS))
    assert signal.valuation_context.margin_of_safety == pytest.approx(0.10)
    assert signal.buy_below_price == pytest.approx(signal.reference_price * 0.9)


def test_the_multi_year_history_is_fetched_once_then_cached(tmp_path):
    """The old design skipped this lookup to keep alert cycles light. It is cached for 30
    days, so only the first cycle pays; later cycles must not refetch."""
    settings = make_settings(tmp_path)
    client = ValuedClient(annual_eps=FLAT_EPS)
    _signal(settings, client)
    _signal(settings, client)
    assert client.history_calls == 1
    assert client.eps_calls == 1


def test_the_rendered_alert_carries_the_confidence_lines(tmp_path):
    text = narrate_signal(_signal(make_settings(tmp_path), ValuedClient(annual_eps=FLAT_EPS)))
    assert "📐 參考合理價" in text
    assert "採計 2 個模型：Target P/E、PEG（成長調整）" in text
    assert "Target P/E 倍數 20×（該股自己的歷史 P/E 中位數）" in text


def test_a_symbol_without_fundamentals_still_alerts_without_a_valuation(tmp_path):
    """FakeClient has no fundamentals at all: the alert must go out, just unvalued."""
    signal = _signal(make_settings(tmp_path), FakeClient())
    assert signal.reference_price is None
    assert signal.valuation_context is None
    assert "參考合理價" not in narrate_signal(signal)
