"""The CLI valuation panel must show the margin actually applied and why (D3)."""
from io import StringIO

from rich.console import Console

from portfolio.market import Fundamentals
from portfolio.report import render_valuation
from portfolio.valuation import ValuationAssumptions, build_valuation_report


def _render(fundamentals, assumptions) -> str:
    buffer = StringIO()
    console = Console(file=buffer, width=140, force_terminal=False)
    report = build_valuation_report("X", 50.0, fundamentals, assumptions)
    render_valuation(console, report, assumptions)
    return buffer.getvalue()


LONE = Fundamentals(symbol="X", values={"trailingEps": 3.0, "forwardEps": 3.1, "sector": "Technology"})


def test_a_widened_margin_is_shown_with_the_reason_and_the_users_original():
    text = _render(LONE, ValuationAssumptions())
    assert "扣 40% 安全邊際後" in text
    assert "不確定性" in text and "只有單一模型適用" in text
    assert "你設的安全邊際 20%，因不確定性自動加寬到 40%" in text


def test_a_users_own_margin_is_shown_as_is_without_the_widening_note():
    text = _render(LONE, ValuationAssumptions(margin_of_safety=0.10, adaptive_margin=False))
    assert "扣 10% 安全邊際後" in text
    assert "自動加寬" not in text


def test_new_models_appear_in_the_cli_table():
    text = _render(LONE, ValuationAssumptions())
    assert "PEG" in text and "DCF" in text
