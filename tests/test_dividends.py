import pandas as pd
import pytest

from conftest import transaction
from portfolio.dividends import forecast_dividends, summarize_received_dividends
from portfolio.holdings import Position
from portfolio.market import Fundamentals


class StubMarketDataClient:
    def __init__(self, rates: dict[str, float], paid_history: dict[str, list[float]] | None = None) -> None:
        self._rates = rates
        self._paid_history = paid_history or {}

    def fundamentals(self, symbol: str) -> Fundamentals:
        return Fundamentals(symbol=symbol, values={"dividendRate": self._rates.get(symbol)})

    def dividend_history(self, symbol: str) -> pd.Series:
        payments = self._paid_history.get(symbol, [])
        if not payments:
            return pd.Series(dtype="float64")
        dates = pd.date_range(end=pd.Timestamp.now(), periods=len(payments), freq="90D")
        return pd.Series(payments, index=dates)


def test_summarize_pairs_dividends_with_their_withholding():
    receipts = summarize_received_dividends([
        transaction(symbol="KO", activity="DIVIDEND", cash_amount=100.0),
        transaction(symbol="KO", activity="WITHHOLDING", cash_amount=-30.0),
    ])
    receipt = receipts["KO"]
    assert receipt.gross_amount == pytest.approx(100.0)
    assert receipt.withheld_amount == pytest.approx(30.0)
    assert receipt.net_amount == pytest.approx(70.0)
    assert receipt.effective_withholding_percent == pytest.approx(30.0)


def test_summarize_accumulates_across_multiple_payments():
    receipts = summarize_received_dividends([
        transaction(symbol="KO", activity="DIVIDEND", cash_amount=50.0, trade_day=1),
        transaction(symbol="KO", activity="DIVIDEND", cash_amount=50.0, trade_day=2),
        transaction(symbol="KO", activity="WITHHOLDING", cash_amount=-15.0, trade_day=1),
        transaction(symbol="KO", activity="WITHHOLDING", cash_amount=-15.0, trade_day=2),
    ])
    assert receipts["KO"].net_amount == pytest.approx(70.0)


def test_summarize_ignores_trades_and_interest():
    receipts = summarize_received_dividends([
        transaction(symbol="AAPL", activity="BUY"),
        transaction(symbol="", activity="INTEREST", cash_amount=3.0),
    ])
    assert receipts == {}


def test_withholding_without_a_matching_dividend_is_still_reported():
    receipts = summarize_received_dividends([
        transaction(symbol="KO", activity="WITHHOLDING", cash_amount=-9.0),
    ])
    assert receipts["KO"].gross_amount == 0.0
    assert receipts["KO"].effective_withholding_percent == 0.0


def test_forecast_applies_withholding_rate_to_gross_income():
    client = StubMarketDataClient({"KO": 2.0})
    forecasts = forecast_dividends(client, [Position("KO", quantity=100)], 0.30)
    assert forecasts[0].gross_annual_income == pytest.approx(200.0)
    assert forecasts[0].net_annual_income == pytest.approx(140.0)


def test_forecast_skips_non_payers():
    client = StubMarketDataClient({"KO": 2.0, "NVDA": 0.0})
    positions = [Position("KO", quantity=10), Position("NVDA", quantity=10)]
    assert [f.symbol for f in forecast_dividends(client, positions, 0.30)] == ["KO"]


def test_forecast_is_sorted_by_income_descending():
    client = StubMarketDataClient({"KO": 2.0, "JNJ": 5.0})
    positions = [Position("KO", quantity=10), Position("JNJ", quantity=10)]
    assert [f.symbol for f in forecast_dividends(client, positions, 0.30)] == ["JNJ", "KO"]


def test_forecast_falls_back_to_trailing_payments_when_no_rate_is_declared():
    """ETFs like VTI have no dividendRate, only a payment history."""
    client = StubMarketDataClient({"VTI": None}, paid_history={"VTI": [0.9, 1.0, 1.0, 1.0]})
    forecasts = forecast_dividends(client, [Position("VTI", quantity=10.05)], 0.30)
    assert forecasts[0].annual_rate_per_share == pytest.approx(3.9)
    assert forecasts[0].gross_annual_income == pytest.approx(10.05 * 3.9)


def test_declared_rate_wins_over_payment_history():
    client = StubMarketDataClient({"MSFT": 3.64}, paid_history={"MSFT": [0.1]})
    forecasts = forecast_dividends(client, [Position("MSFT", quantity=10)], 0.30)
    assert forecasts[0].annual_rate_per_share == pytest.approx(3.64)


def test_symbol_with_neither_rate_nor_history_is_skipped():
    client = StubMarketDataClient({"BRK-B": None})
    assert forecast_dividends(client, [Position("BRK-B", quantity=10)], 0.30) == []
