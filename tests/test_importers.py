from pathlib import Path

import pytest

from portfolio.holdings import build_ledger
from portfolio.importers import (
    FORMATS,
    ImportFormatError,
    UnknownImportFormatError,
    get_format,
    list_formats,
)

TEMPLATES = Path(__file__).parent.parent / "src/portfolio/web/static/templates"


def _write(tmp_path, text: str) -> Path:
    path = tmp_path / "trades.csv"
    path.write_text(text, encoding="utf-8")
    return path


def _parse(key: str, path: Path):
    return get_format(key).parser(path)


def test_registry_groups_formats_by_market():
    by_market = {fmt.key: fmt.market_code for fmt in list_formats()}
    assert by_market == {"firstrade": "US", "generic-us": "US", "generic-tw": "TW"}
    assert get_format(None).key == "firstrade"


def test_unknown_format_is_named():
    with pytest.raises(UnknownImportFormatError, match="nope"):
        get_format("nope")


@pytest.mark.parametrize("key", ["generic-us", "generic-tw"])
def test_shipped_templates_parse_under_their_own_format(key):
    transactions = _parse(key, TEMPLATES / FORMATS[key].template)
    assert [t.activity for t in transactions] == ["BUY", "DIVIDEND", "SELL"]


def test_taiwan_trades_build_a_twd_position_with_sale_tax_in_costs(tmp_path):
    path = _write(
        tmp_path,
        "日期,代號,動作,股數,價格,手續費,稅,備註\n"
        "2026/01/15,2330.TW,買進,1000,600,855,0,\n"
        "2026/06/02,2330.TW,賣出,400,720,410,864,\n",
    )
    transactions = _parse("generic-tw", path)
    buy, sell = transactions
    assert buy.cash_amount == pytest.approx(-(1000 * 600 + 855))
    assert sell.cash_amount == pytest.approx(400 * 720 - 410 - 864)
    ledger = build_ledger(transactions)
    position = ledger.positions["2330.TW"]
    assert position.quantity == pytest.approx(600)
    avg = (1000 * 600 + 855) / 1000
    assert position.realized_gain == pytest.approx(400 * 720 - 410 - 864 - avg * 400)


def test_taiwan_dividend_keeps_gross_and_withheld_apart(tmp_path):
    path = _write(
        tmp_path,
        "date,symbol,action,quantity,price,fee,tax,note\n"
        "2026-03-10,2330.TW,DIVIDEND,,25000,0,527,\n",
    )
    (dividend,) = _parse("generic-tw", path)
    assert dividend.cash_amount == 25000
    assert dividend.withheld_amount == 527


def test_taiwan_format_rejects_a_bare_code_with_the_fix(tmp_path):
    path = _write(tmp_path, "date,symbol,action,quantity,price\n2026-01-15,2330,BUY,1000,600\n")
    with pytest.raises(ImportFormatError, match=r"2330\.TW"):
        _parse("generic-tw", path)


def test_taiwan_format_rejects_a_us_symbol(tmp_path):
    path = _write(tmp_path, "date,symbol,action,quantity,price\n2026-01-15,AAPL,BUY,1,100\n")
    with pytest.raises(ImportFormatError, match="美股"):
        _parse("generic-tw", path)


def test_us_generic_rejects_a_taiwan_symbol(tmp_path):
    path = _write(tmp_path, "date,symbol,action,quantity,price\n2026-01-15,2330.TW,BUY,1,100\n")
    with pytest.raises(ImportFormatError, match="台股"):
        _parse("generic-us", path)


def test_errors_list_row_numbers_and_nothing_is_partially_imported(tmp_path):
    path = _write(
        tmp_path,
        "date,symbol,action,quantity,price\n"
        "2026-01-15,AAPL,BUY,1,100\n"
        "not-a-date,AAPL,BUY,1,100\n"
        "2026-01-17,AAPL,HODL,1,100\n"
        "2026-01-18,AAPL,BUY,0,100\n",
    )
    with pytest.raises(ImportFormatError) as raised:
        _parse("generic-us", path)
    message = str(raised.value)
    assert "第 3 列" in message and "第 4 列" in message and "第 5 列" in message
    assert "第 2 列" not in message


def test_missing_required_column_asks_for_the_template(tmp_path):
    path = _write(tmp_path, "foo,bar\n1,2\n")
    with pytest.raises(ImportFormatError, match="範本"):
        _parse("generic-us", path)


def test_identical_fills_on_one_day_both_survive_but_reimport_dedupes(tmp_path):
    path = _write(
        tmp_path,
        "date,symbol,action,quantity,price\n"
        "2026-01-15,AAPL,BUY,1,100\n2026-01-15,AAPL,BUY,1,100\n",
    )
    first = _parse("generic-us", path)
    second = _parse("generic-us", path)
    assert len({t.row_fingerprint for t in first}) == 2
    assert [t.row_fingerprint for t in first] == [t.row_fingerprint for t in second]


def test_chinese_and_english_headers_and_actions_are_equivalent(tmp_path):
    english = _write(tmp_path, "date,symbol,action,quantity,price\n2026-01-15,AAPL,BUY,2,100\n")
    chinese = tmp_path / "zh.csv"
    chinese.write_text("日期,代號,動作,股數,價格\n2026/01/15,AAPL,買進,2,100\n", encoding="utf-8")
    a, b = _parse("generic-us", english)[0], _parse("generic-us", chinese)[0]
    assert (a.trade_date, a.symbol, a.activity, a.quantity, a.price) == (
        b.trade_date, b.symbol, b.activity, b.quantity, b.price)
