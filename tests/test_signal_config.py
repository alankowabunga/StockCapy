from conftest import make_settings
from portfolio.signal_config import (
    GLOBAL_DEFAULT_KEY,
    ThresholdOverride,
    delete_threshold_override,
    load_global_thresholds,
    resolve_thresholds,
    save_threshold_override,
)
from portfolio.signals import SignalThresholds
from portfolio.store import open_store


def _conn(tmp_path):
    return open_store(make_settings(tmp_path).database_path)


def test_defaults_apply_when_nothing_is_stored(tmp_path):
    with _conn(tmp_path) as connection:
        resolved = resolve_thresholds(connection, "NVDA")
    assert resolved.source == "default"
    assert resolved.thresholds == SignalThresholds()


def test_a_symbol_override_layers_on_top_of_the_defaults(tmp_path):
    with _conn(tmp_path) as connection:
        save_threshold_override(connection, ThresholdOverride("NVDA", rsi_oversold=25.0))
        resolved = resolve_thresholds(connection, "NVDA")
    assert resolved.source == "manual"
    assert resolved.thresholds.rsi_oversold == 25.0
    # Untouched fields keep the default.
    assert resolved.thresholds.short_trend_bias_percent == SignalThresholds().short_trend_bias_percent


def test_global_default_is_overridden_then_the_symbol_on_top(tmp_path):
    with _conn(tmp_path) as connection:
        save_threshold_override(connection, ThresholdOverride(GLOBAL_DEFAULT_KEY, rsi_oversold=28.0))
        save_threshold_override(connection, ThresholdOverride("NVDA", short_trend_bias_percent=15.0))
        resolved = resolve_thresholds(connection, "NVDA")
        default = load_global_thresholds(connection)
    assert default.rsi_oversold == 28.0
    assert resolved.thresholds.rsi_oversold == 28.0  # inherited from global
    assert resolved.thresholds.short_trend_bias_percent == 15.0  # symbol override


def test_partial_overrides_merge_rather_than_replace(tmp_path):
    with _conn(tmp_path) as connection:
        save_threshold_override(connection, ThresholdOverride("NVDA", rsi_oversold=25.0))
        save_threshold_override(connection, ThresholdOverride("NVDA", rsi_overbought=75.0))
        resolved = resolve_thresholds(connection, "NVDA")
    assert resolved.thresholds.rsi_oversold == 25.0
    assert resolved.thresholds.rsi_overbought == 75.0


def test_reset_returns_to_default(tmp_path):
    with _conn(tmp_path) as connection:
        save_threshold_override(connection, ThresholdOverride("NVDA", rsi_oversold=25.0))
        assert delete_threshold_override(connection, "NVDA") is True
        assert resolve_thresholds(connection, "NVDA").source == "default"


def test_empty_override_is_detected():
    assert ThresholdOverride("NVDA").is_empty()
    assert not ThresholdOverride("NVDA", rsi_oversold=25.0).is_empty()
