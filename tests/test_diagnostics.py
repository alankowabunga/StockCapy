from datetime import date

import pytest

from conftest import transaction
from portfolio.diagnostics import (
    detect_missing_cost_basis,
    extract_record_date,
    extract_record_date_shares,
)


@pytest.mark.parametrize(
    "description,expected",
    [
        ("CASH DIV ON 10.05 SHS REC 04/15/26", 10.05),
        ("CASH DIV ON 6 SHS", 6.0),
        ("CASH DIV ON 1,234.5 SHS", 1234.5),
        ("INTEREST ON CREDIT BALANCE", None),
    ],
)
def test_extract_record_date_shares(description, expected):
    result = extract_record_date_shares(description)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_detects_when_dividend_implies_more_shares_than_buys_explain():
    """A dividend on ~10 shares with only a tiny DRIP buy means the rest predates the export."""
    transactions = [
        transaction(symbol="VTI", activity="DIVIDEND", quantity=0, cash_amount=15.00,
                    description="CASH DIV ON 10.05 SHS", trade_day=2),
        transaction(symbol="VTI", activity="BUY", quantity=0.04, price=375.00, trade_day=3),
    ]
    warnings = detect_missing_cost_basis(transactions)
    assert len(warnings) == 1
    assert warnings[0].symbol == "VTI"
    assert warnings[0].missing_shares == pytest.approx(10.01, abs=1e-4)


def test_no_warning_when_buys_cover_the_dividend_shares():
    transactions = [
        transaction(symbol="MSFT", activity="BUY", quantity=20, price=400.0, trade_day=1),
        transaction(symbol="MSFT", activity="DIVIDEND", quantity=0, cash_amount=20.00,
                    description="CASH DIV ON 20.005 SHS", trade_day=2),
    ]
    assert detect_missing_cost_basis(transactions) == []


def test_no_warning_for_dividends_without_a_share_count():
    transactions = [
        transaction(symbol="", activity="INTEREST", cash_amount=0.73,
                    description="INTEREST ON CREDIT BALANCE AT 0.150%"),
    ]
    assert detect_missing_cost_basis(transactions) == []


def test_warnings_are_sorted_by_severity():
    transactions = [
        transaction(symbol="AAA", activity="DIVIDEND", description="CASH DIV ON 5 SHS", trade_day=1),
        transaction(symbol="BBB", activity="DIVIDEND", description="CASH DIV ON 100 SHS", trade_day=1),
    ]
    assert [w.symbol for w in detect_missing_cost_basis(transactions)] == ["BBB", "AAA"]


def test_a_reinvestment_after_the_record_date_is_not_subtracted_from_it():
    """The real trap: the April dividend's 10.05 shares already include the January
    reinvestment, so subtracting today's holdings counts that 0.05 twice and
    under-reports the gap as 9.96 instead of 10.0."""
    transactions = [
        transaction(symbol="VTI", activity="DIVIDEND", quantity=0, cash_amount=15.00,
                    description="CASH DIV ON 10.00000 SHS REC 01/15/26 PAY 01/20/26",
                    trade_date=date(2026, 1, 20)),
        transaction(symbol="VTI", activity="BUY", quantity=0.05, price=300.00,
                    trade_date=date(2026, 1, 20)),
        transaction(symbol="VTI", activity="DIVIDEND", quantity=0, cash_amount=15.00,
                    description="CASH DIV ON 10.05000 SHS REC 04/15/26 PAY 04/20/26",
                    trade_date=date(2026, 4, 20)),
        transaction(symbol="VTI", activity="BUY", quantity=0.04, price=375.00,
                    trade_date=date(2026, 4, 20)),
    ]
    warnings = detect_missing_cost_basis(transactions)
    assert len(warnings) == 1
    assert warnings[0].missing_shares == pytest.approx(10.0, abs=1e-5)


def test_record_dates_are_parsed_as_two_digit_years():
    assert extract_record_date("CASH DIV ON 6 SHS REC 04/15/26 PAY 04/20/26") == date(2026, 4, 15)
    assert extract_record_date("CASH DIV ON 6 SHS") is None


def test_both_record_dates_agree_on_the_same_missing_block():
    """Two dividends three months apart must imply the same pre-window holding; if they
    disagree the parsing is wrong, so this pins the reconciliation."""
    january = transaction(symbol="VTI", activity="DIVIDEND",
                          description="CASH DIV ON 10.00000 SHS REC 01/15/26",
                          trade_date=date(2026, 1, 20))
    april = transaction(symbol="VTI", activity="DIVIDEND",
                        description="CASH DIV ON 10.05000 SHS REC 04/15/26",
                        trade_date=date(2026, 4, 20))
    drip = transaction(symbol="VTI", activity="BUY", quantity=0.05, price=300.00,
                       trade_date=date(2026, 1, 20))
    from_january = detect_missing_cost_basis([january, drip])[0].missing_shares
    from_april = detect_missing_cost_basis([april, drip])[0].missing_shares
    assert from_january == pytest.approx(from_april, abs=1e-5)
