import io
from types import SimpleNamespace

from rich.console import Console

from portfolio.report import _render_entry_price_table


def _target(symbol: str) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        last_price=100.0,
        average_cost=90.0,
        discount_to_average_cost_percent=11.1,
        reference_price=120.0,
        buy_below=102.0,
        gap_to_buy_below_percent=-2.0,
        basis="估值模型",
        model_count=2,
    )


def _render(symbol: str) -> str:
    buffer = io.StringIO()
    _render_entry_price_table(Console(file=buffer, width=200), [_target(symbol)])
    return buffer.getvalue()


def test_entry_price_table_uses_nt_dollar_for_a_taiwan_symbol():
    out = _render("2330.TW")
    assert "NT$102.00" in out  # buy-below price in TWD


def test_entry_price_table_keeps_the_plain_dollar_for_us():
    out = _render("AAPL")
    assert "$102.00" in out
    assert "NT$" not in out
