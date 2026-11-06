import pytest

from portfolio.firstrade_csv import (
    UnrecognizedCsvLayoutError,
    build_column_map,
    classify_activity,
    load_transaction_csv,
    parse_amount,
    parse_date,
    parse_transaction_rows,
)

FIRSTRADE_HEADERS = [
    "Symbol", "Quantity", "Price", "Action", "Description", "TradeDate",
    "SettledDate", "Interest", "Amount", "Commission", "Fee", "CUSIP", "RecordType",
]


def test_build_column_map_matches_firstrade_headers():
    column_map = build_column_map(FIRSTRADE_HEADERS)
    assert column_map["trade_date"] == "TradeDate"
    assert column_map["cash_amount"] == "Amount"
    assert column_map["activity"] == "Action"


def test_build_column_map_tolerates_spacing_and_case():
    column_map = build_column_map(["trade date", "SYMBOL", "Net Amount"])
    assert column_map["trade_date"] == "trade date"
    assert column_map["symbol"] == "SYMBOL"
    assert column_map["cash_amount"] == "Net Amount"


@pytest.mark.parametrize(
    "raw,expected",
    [("1,234.50", 1234.50), ("(99.10)", -99.10), ("$5", 5.0), ("", 0.0), ("--", 0.0), ("abc", 0.0)],
)
def test_parse_amount_handles_broker_formats(raw, expected):
    assert parse_amount(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["2026-01-15", "01/15/2026", "1/15/26", "2026/01/15"])
def test_parse_date_accepts_common_layouts(raw):
    assert parse_date(raw).year == 2026


def test_parse_date_returns_none_for_junk():
    assert parse_date("not a date") is None
    assert parse_date("") is None


@pytest.mark.parametrize(
    "action,description,expected",
    [
        ("BUY", "BOUGHT 10 AAPL", "BUY"),
        ("SELL", "SOLD 5 MSFT", "SELL"),
        ("Dividend", "CASH DIV KO", "DIVIDEND"),
        ("Other", "NRA WITHHOLDING KO", "WITHHOLDING"),
        ("Other", "FOREIGN TAX WITHHELD", "WITHHOLDING"),
        ("Interest", "CREDIT INTEREST", "INTEREST"),
        ("Other", "ACH DEPOSIT", "TRANSFER"),
        ("", "SOMETHING UNKNOWN", "OTHER"),
    ],
)
def test_classify_activity(action, description, expected):
    assert classify_activity(action, description) == expected


def test_withholding_wins_over_dividend_when_both_words_present():
    """A 'NRA WITHHOLDING ON DIVIDEND' row is tax, not income."""
    assert classify_activity("Other", "NRA WITHHOLDING ON DIVIDEND") == "WITHHOLDING"


def test_parse_transaction_rows_skips_rows_without_a_date():
    rows = [
        {"TradeDate": "01/15/2026", "Symbol": "AAPL", "Action": "BUY", "Quantity": "10",
         "Price": "100", "Amount": "(1000.00)", "Description": "BOUGHT"},
        {"TradeDate": "", "Symbol": "", "Action": "", "Quantity": "", "Price": "",
         "Amount": "", "Description": "total row"},
    ]
    parsed = parse_transaction_rows(rows, FIRSTRADE_HEADERS)
    assert len(parsed) == 1
    assert parsed[0].symbol == "AAPL"
    assert parsed[0].cash_amount == pytest.approx(-1000.0)


def test_parse_transaction_rows_rejects_unrecognizable_layout():
    with pytest.raises(UnrecognizedCsvLayoutError):
        parse_transaction_rows([{"foo": "bar"}], ["foo"])


def test_row_fingerprint_is_stable_and_distinguishes_rows():
    rows = [
        {"TradeDate": "01/15/2026", "Symbol": "AAPL", "Action": "BUY", "Quantity": "10",
         "Price": "100", "Amount": "(1000.00)", "Description": "BOUGHT"},
    ]
    first = parse_transaction_rows(rows, FIRSTRADE_HEADERS)[0]
    second = parse_transaction_rows(rows, FIRSTRADE_HEADERS)[0]
    assert first.row_fingerprint == second.row_fingerprint
    other_rows = [{**rows[0], "Quantity": "11"}]
    assert parse_transaction_rows(other_rows, FIRSTRADE_HEADERS)[0].row_fingerprint != first.row_fingerprint


def test_load_transaction_csv_reads_a_real_file(tmp_path):
    csv_path = tmp_path / "history.csv"
    csv_path.write_text(
        "Symbol,Quantity,Price,Action,Description,TradeDate,Amount\n"
        "AAPL,10,100.00,BUY,BOUGHT AAPL,01/15/2026,(1000.00)\n"
        "KO,0,0,Dividend,CASH DIV KO,02/01/2026,21.20\n",
        encoding="utf-8",
    )
    transactions = load_transaction_csv(csv_path)
    assert [t.activity for t in transactions] == ["BUY", "DIVIDEND"]


CHINESE_HEADERS = ["日期", "交易類別", "數量", "說明", "代號", "賬戶類別", "價格", "金額"]


def test_build_column_map_matches_chinese_export_headers():
    column_map = build_column_map(CHINESE_HEADERS)
    assert column_map["trade_date"] == "日期"
    assert column_map["cash_amount"] == "金額"
    assert column_map["activity"] == "交易類別"
    assert column_map["description"] == "說明"


@pytest.mark.parametrize(
    "activity,expected",
    [("買進", "BUY"), ("賣出", "SELL"), ("股息", "DIVIDEND"), ("利息收入", "INTEREST"), ("存款", "TRANSFER")],
)
def test_chinese_activity_names_map_directly(activity, expected):
    assert classify_activity(activity, "") == expected


def test_dividend_reinvestment_row_is_a_buy_despite_being_labeled_other():
    """Firstrade files DRIP purchases under 其他 with the price only in the text."""
    description = "MICROSOFT CORP REIN @ 400.0000 REC 08/14/26 PAY 09/11/26"
    assert classify_activity("其他", description) == "BUY"


def test_statement_fee_row_is_a_fee():
    assert classify_activity("其他", "JAN PAPER STATEMENT FEE") == "FEE"


def test_internal_cash_transfer_is_a_transfer():
    assert classify_activity("其他", "XFER MARGIN TO CASH") == "TRANSFER"


@pytest.mark.parametrize(
    "description,expected",
    [
        ("CASH DIV ON 20.00000 SHS NON-RES TAX WITHHELD $6.00000", 6.00),
        ("CASH DIV ON 10.05000 SHS NON-QUALIFIED DIVIDEND $4.50000", 4.50),
        ("CASH DIV ON 6 SHS", 0.0),
    ],
)
def test_extract_withheld_amount_reads_tax_from_description(description, expected):
    from portfolio.firstrade_csv import extract_withheld_amount

    assert extract_withheld_amount(description) == pytest.approx(expected)


def test_extract_reinvestment_price_reads_price_from_description():
    from portfolio.firstrade_csv import extract_reinvestment_price

    assert extract_reinvestment_price("VTI REIN @ 375.0000 REC 04/15/26") == pytest.approx(375.0)
    assert extract_reinvestment_price("BOUGHT MSFT") == 0.0


def test_chinese_export_parses_end_to_end(tmp_path):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        '"日期","交易類別","數量","說明","代號","賬戶類別","價格","金額"\n'
        '"9/11/2026","股息","0","MSFT CASH DIV ON 20.00000 SHS NON-RES TAX WITHHELD $6.00000","MSFT","融資","0.00","20.00"\n'
        '"9/11/2026","其他","0.035","MSFT REIN @ 400.0000 REC 08/14/26","MSFT","融資","0.00","-14.00"\n'
        '"6/15/2026","買進","5","MSFT UNSOLICITED","MSFT","融資","396.65","-1,983.25"\n',
        encoding="utf-8",
    )
    transactions = load_transaction_csv(csv_path)
    assert [t.activity for t in transactions] == ["DIVIDEND", "BUY", "BUY"]
    assert transactions[0].withheld_amount == pytest.approx(6.00)
    assert transactions[1].price == pytest.approx(400.0)
    assert transactions[2].price == pytest.approx(396.65)


def test_reinvested_shares_add_to_the_position():
    """Gross dividend minus withholding must equal the reinvested cash."""
    from portfolio.firstrade_csv import extract_withheld_amount

    gross, reinvested = 20.00, 14.00
    assert gross - extract_withheld_amount("NON-RES TAX WITHHELD $6.00000") == pytest.approx(reinvested)
