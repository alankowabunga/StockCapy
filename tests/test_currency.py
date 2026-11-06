from datetime import date

import pandas as pd
import pytest

from portfolio.currency import (
    CurrencyAttribution,
    pair_name,
    pair_symbol,
    rate_on_or_before,
    rate_series_to_lookup,
)


def test_pair_naming_matches_yahoo():
    assert pair_symbol("TWD") == "USDTWD=X"
    assert pair_symbol("twd") == "USDTWD=X"
    assert pair_name("TWD") == "USDTWD"


def test_attribution_parts_sum_exactly_to_the_total():
    """The split is only trustworthy if it reconciles; this is the load-bearing test."""
    attribution = CurrencyAttribution(
        cost_base=10000.0, cost_home=320000.0, market_value_base=12000.0, rate_now=31.765
    )
    parts = attribution.stock_gain_home + attribution.currency_gain_home
    assert parts == pytest.approx(attribution.total_gain_home)


@pytest.mark.parametrize(
    "cost_base,cost_home,market_base,rate_now",
    [
        (10000.0, 320000.0, 12000.0, 31.765),
        (5000.0, 150000.0, 4000.0, 33.0),
        (1234.56, 39000.0, 1234.56, 30.0),
        (100.0, 3200.0, 250.0, 32.0),
    ],
)
def test_attribution_reconciles_across_scenarios(cost_base, cost_home, market_base, rate_now):
    attribution = CurrencyAttribution(cost_base, cost_home, market_base, rate_now)
    assert attribution.stock_gain_home + attribution.currency_gain_home == pytest.approx(
        attribution.total_gain_home
    )


def test_average_cost_rate_is_the_weighted_rate_paid():
    attribution = CurrencyAttribution(10000.0, 320000.0, 10000.0, 31.0)
    assert attribution.average_cost_rate == pytest.approx(32.0)


def test_pure_currency_move_produces_no_stock_gain():
    """Share price unchanged, TWD weaker: all of the gain is FX."""
    attribution = CurrencyAttribution(10000.0, 310000.0, 10000.0, 32.0)
    assert attribution.stock_gain_home == pytest.approx(0.0)
    assert attribution.currency_gain_home == pytest.approx(10000.0 * (32.0 - 31.0))
    assert attribution.total_gain_home == pytest.approx(10000.0)


def test_pure_stock_move_produces_no_currency_gain():
    attribution = CurrencyAttribution(10000.0, 320000.0, 11000.0, 32.0)
    assert attribution.currency_gain_home == pytest.approx(0.0)
    assert attribution.stock_gain_home == pytest.approx(1000.0 * 32.0)


def test_stock_gain_can_be_offset_by_a_currency_loss():
    """A rising share price with a strengthening TWD can still lose money in TWD."""
    attribution = CurrencyAttribution(10000.0, 350000.0, 10200.0, 33.0)
    assert attribution.stock_gain_home > 0
    assert attribution.currency_gain_home < 0
    assert attribution.total_gain_home < 0


def test_zero_cost_position_reports_no_average_rate():
    attribution = CurrencyAttribution(0.0, 0.0, 100.0, 31.0)
    assert attribution.average_cost_rate is None
    assert attribution.currency_gain_home == 0.0
    assert attribution.total_return_percent == 0.0


def test_rate_lookup_normalizes_dates_and_drops_time():
    series = pd.Series(
        [31.5, 31.6],
        index=pd.to_datetime(["2026-04-01 09:30:00", "2026-04-02 09:30:00"]),
    )
    assert rate_series_to_lookup(series) == {
        "2026-04-01": pytest.approx(31.5),
        "2026-04-02": pytest.approx(31.6),
    }


def test_rate_lookup_handles_timezone_aware_index():
    series = pd.Series([31.5], index=pd.to_datetime(["2026-04-01 09:30:00-04:00"]))
    assert list(rate_series_to_lookup(series)) == ["2026-04-01"]


def test_empty_series_yields_an_empty_lookup():
    assert rate_series_to_lookup(pd.Series(dtype="float64")) == {}


def test_exact_date_is_returned_when_present():
    rates = {"2026-09-14": 31.8, "2026-09-15": 31.9}
    assert rate_on_or_before(rates, date(2026, 9, 15)) == pytest.approx(31.9)


def test_weekend_trade_falls_back_to_the_prior_session():
    """FX has no Saturday quote; the Friday rate is the right answer."""
    rates = {"2026-09-11": 31.7}
    assert rate_on_or_before(rates, date(2026, 9, 13)) == pytest.approx(31.7)


def test_lookup_gives_up_beyond_the_window():
    assert rate_on_or_before({"2026-09-11": 31.7}, date(2026, 10, 1)) is None


def test_lookup_never_uses_a_future_rate():
    """Using tomorrow's rate for today's trade would fabricate a gain."""
    assert rate_on_or_before({"2026-09-20": 31.7}, date(2026, 9, 15)) is None


def test_taiwan_dollar_uses_the_nt_prefix():
    """A bare '$' next to a TWD figure reads as USD and misleads."""
    from portfolio.currency import currency_symbol

    assert currency_symbol("TWD") == "NT$"
    assert currency_symbol("twd") == "NT$"


def test_us_dollar_keeps_the_plain_sign():
    from portfolio.currency import currency_symbol

    assert currency_symbol("USD") == "$"


def test_unmapped_currency_falls_back_to_its_code():
    from portfolio.currency import currency_symbol

    assert currency_symbol("SGD") == "SGD "
    assert currency_symbol("") == " "
