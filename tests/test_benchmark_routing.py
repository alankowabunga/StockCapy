from conftest import make_settings

from portfolio.markets import TW_MARKET, US_MARKET
from portfolio.service import benchmark_symbol_for


def test_us_signals_compare_against_the_us_benchmark(tmp_path):
    assert benchmark_symbol_for(make_settings(tmp_path), US_MARKET) == "SPY"


def test_taiwan_signals_compare_against_the_taiwan_benchmark(tmp_path):
    # A Taiwan signal must not be judged against the S&P 500.
    assert benchmark_symbol_for(make_settings(tmp_path), TW_MARKET) == "^TWII"
