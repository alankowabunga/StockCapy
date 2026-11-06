import pytest

from portfolio.markets import (
    TW_MARKET,
    US_MARKET,
    InvalidSymbolError,
    currency_for_symbol,
    is_taiwan_symbol,
    market_for_symbol,
    resolve_symbol_input,
)


@pytest.mark.parametrize(
    "symbol, market",
    [
        ("AAPL", US_MARKET),
        ("MSFT", US_MARKET),
        ("2330.TW", TW_MARKET),
        ("6488.TWO", TW_MARKET),
        ("2330.tw", TW_MARKET),  # suffix match is case-insensitive
        ("", US_MARKET),  # unknown falls back to US, never crashes
    ],
)
def test_market_is_read_from_the_suffix(symbol, market):
    assert market_for_symbol(symbol) is market
    assert is_taiwan_symbol(symbol) is (market is TW_MARKET)


def test_currency_follows_the_market():
    assert currency_for_symbol("AAPL") == "USD"
    assert currency_for_symbol("2330.TW") == "TWD"
    assert currency_for_symbol("6488.TWO") == "TWD"


def test_us_and_already_suffixed_symbols_pass_through_uppercased():
    assert resolve_symbol_input("aapl", has_price=None) == "AAPL"
    # A dotted code is not a bare Taiwan code, so it never consults has_price.
    assert resolve_symbol_input("2330.two", has_price=None) == "2330.TWO"
    assert resolve_symbol_input("  tsm  ", has_price=None) == "TSM"


def test_bare_taiwan_code_resolves_to_the_listing_with_a_price():
    assert resolve_symbol_input("2330", has_price=lambda s: s == "2330.TW") == "2330.TW"
    assert resolve_symbol_input("6488", has_price=lambda s: s == "6488.TWO") == "6488.TWO"


def test_bare_code_prefers_listed_tw_when_both_have_a_price():
    assert resolve_symbol_input("2330", has_price=lambda s: True) == "2330.TW"


def test_bare_code_with_no_price_anywhere_is_rejected():
    with pytest.raises(InvalidSymbolError, match="2330"):
        resolve_symbol_input("2330", has_price=lambda s: False)


def test_bare_code_without_a_price_checker_asks_for_an_explicit_suffix():
    # Offline / no way to check: refuse rather than guess the exchange.
    with pytest.raises(InvalidSymbolError, match=r"\.TW"):
        resolve_symbol_input("2330", has_price=None)


def test_five_digit_etf_code_is_treated_as_a_bare_taiwan_code():
    assert resolve_symbol_input("00878", has_price=lambda s: s == "00878.TW") == "00878.TW"
