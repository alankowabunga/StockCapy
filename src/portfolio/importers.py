"""Import formats, grouped by the market the broker trades (D32).

The user picks the format before uploading — never auto-detected, because guessing a
layout wrong would silently misread money. Each format owns its parser, so a new
broker is one registry entry, not a branch inside the parsing code."""

from __future__ import annotations

import csv
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .firstrade_csv import (
    BrokerageTransaction,
    UnrecognizedCsvLayoutError,
    load_transaction_csv,
    parse_amount,
    parse_date,
)
from .markets import TW_MARKET, US_MARKET, is_taiwan_symbol

DEFAULT_FORMAT_KEY = "firstrade"
MAX_REPORTED_ERRORS = 10

GENERIC_COLUMNS = {
    "date": ("date", "tradedate", "日期", "交易日期"),
    "symbol": ("symbol", "ticker", "代號", "代号", "股票代號"),
    "action": ("action", "type", "activity", "動作", "类别", "類別", "交易類別"),
    "quantity": ("quantity", "qty", "shares", "股數", "股数", "數量", "数量"),
    "price": ("price", "unitprice", "價格", "价格", "單價"),
    "fee": ("fee", "fees", "commission", "手續費", "手续费"),
    "tax": ("tax", "稅", "税", "證交稅", "稅額"),
    "note": ("note", "memo", "備註", "备注"),
}
REQUIRED_GENERIC_COLUMNS = ("date", "symbol", "action")

ACTION_ALIASES = {
    "BUY": "BUY",
    "買進": "BUY",
    "买进": "BUY",
    "買": "BUY",
    "SELL": "SELL",
    "賣出": "SELL",
    "卖出": "SELL",
    "賣": "SELL",
    "DIVIDEND": "DIVIDEND",
    "DIV": "DIVIDEND",
    "股利": "DIVIDEND",
    "股息": "DIVIDEND",
    "配息": "DIVIDEND",
}


class ImportFormatError(ValueError):
    """The file is readable but a row (or the header) does not fit the chosen format."""


class UnknownImportFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ImportFormat:
    key: str
    market_code: str
    label: str
    instructions: str
    parser: Callable[[Path], list[BrokerageTransaction]]
    template: str | None = None
    """File name under static/templates/, offered as a download."""


def _normalize_header(header: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]", "", (header or "").strip().lower())


def _generic_column_map(headers: list[str]) -> dict[str, str]:
    normalized = {_normalize_header(header): header for header in headers}
    found: dict[str, str] = {}
    for field, aliases in GENERIC_COLUMNS.items():
        for alias in aliases:
            if alias in normalized:
                found[field] = normalized[alias]
                break
    return found


def _check_symbol(symbol: str, market_code: str) -> str | None:
    """None when fine, else why not. Taiwan needs the full Yahoo code: guessing 上市 vs
    上櫃 at import time would silently price the wrong security."""
    if market_code == TW_MARKET.code:
        if is_taiwan_symbol(symbol):
            return None
        if re.match(r"^\d{4,6}[A-Z]?$", symbol):
            return f"台股代號請寫完整，例如 {symbol}.TW（上市）或 {symbol}.TWO（上櫃）"
        return f"{symbol} 不是台股代號（結尾要有 .TW 或 .TWO）；美股請用「美股券商」區匯入"
    if is_taiwan_symbol(symbol):
        return f"{symbol} 是台股代號，請改用「台股券商」區匯入"
    return None


def _build_row(
    row: dict[str, str], columns: dict[str, str], market_code: str
) -> tuple[BrokerageTransaction | None, str | None]:
    read = lambda field: (row.get(columns[field], "") if field in columns else "")  # noqa: E731
    if not any((value or "").strip() for value in row.values()):
        return None, None  # a blank line is not an error
    trade_date = parse_date(read("date"))
    if trade_date is None:
        return None, f"日期「{read('date')}」看不懂（請用 YYYY-MM-DD 或 YYYY/MM/DD）"
    symbol = read("symbol").strip().upper()
    if not symbol:
        return None, "缺少代號"
    problem = _check_symbol(symbol, market_code)
    if problem:
        return None, problem
    activity = ACTION_ALIASES.get(read("action").strip().upper()) or ACTION_ALIASES.get(
        read("action").strip()
    )
    if activity is None:
        return None, f"動作「{read('action')}」看不懂（BUY/SELL/DIVIDEND 或 買進/賣出/股利）"
    return _transaction_for(activity, trade_date, symbol, read), None


def _transaction_for(activity: str, trade_date: date, symbol: str, read) -> BrokerageTransaction:
    quantity = abs(parse_amount(read("quantity")))
    price = abs(parse_amount(read("price")))
    fee = abs(parse_amount(read("fee")))
    tax = abs(parse_amount(read("tax")))
    note = read("note").strip()
    if activity == "DIVIDEND":
        # price is the whole dividend, because Taiwan brokers report one cash figure
        # per payment rather than a per-share rate. tax is what was held back.
        return BrokerageTransaction(
            trade_date=trade_date, settled_date=None, symbol=symbol, activity="DIVIDEND",
            quantity=0.0, price=0.0, cash_amount=price, commission=0.0, fee=0.0,
            description=note or "股利", withheld_amount=tax,
        )
    gross = quantity * price
    # A sale's transaction tax (證交稅) is a cost of selling, so it joins the fee.
    charges = fee + (tax if activity == "SELL" else 0.0)
    cash = -(gross + charges) if activity == "BUY" else gross - charges
    return BrokerageTransaction(
        trade_date=trade_date, settled_date=None, symbol=symbol, activity=activity,
        quantity=quantity, price=price, cash_amount=cash, commission=fee,
        fee=tax if activity == "SELL" else 0.0, description=note or activity,
    )


def _validate_amounts(transaction: BrokerageTransaction) -> str | None:
    if transaction.activity in {"BUY", "SELL"}:
        if transaction.quantity <= 0:
            return "買賣的股數要大於 0"
        if transaction.price <= 0:
            return "買賣的價格要大於 0"
    elif transaction.cash_amount <= 0:
        return "股利的「價格」欄要填這筆股利的總金額（大於 0）"
    return None


def _distinguish_repeats(transactions: list[BrokerageTransaction]) -> list[BrokerageTransaction]:
    """Two genuinely identical fills on one day (common in odd-lot trading) would share a
    fingerprint and the second be dropped as a re-import. Numbering repeats within the
    file keeps both, and stays stable so re-importing the same file still dedupes."""
    import dataclasses

    seen: dict[str, int] = {}
    result = []
    for transaction in transactions:
        key = transaction.row_fingerprint
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            transaction = dataclasses.replace(
                transaction, description=f"{transaction.description} #{seen[key]}"
            )
        result.append(transaction)
    return result


def _parse_generic(csv_path: Path, market_code: str) -> list[BrokerageTransaction]:
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    columns = _generic_column_map(headers)
    missing = [name for name in REQUIRED_GENERIC_COLUMNS if name not in columns]
    if missing:
        raise ImportFormatError(
            f"缺少必要欄位：{'、'.join(missing)}（看到的欄位：{headers}）。可先下載範本對照。"
        )
    transactions: list[BrokerageTransaction] = []
    errors: list[str] = []
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        transaction, problem = _build_row(row, columns, market_code)
        problem = problem or (_validate_amounts(transaction) if transaction else None)
        if problem:
            errors.append(f"第 {number} 列：{problem}")
        elif transaction:
            transactions.append(transaction)
    if errors:
        shown = errors[:MAX_REPORTED_ERRORS]
        more = len(errors) - len(shown)
        suffix = f"\n…還有 {more} 列有問題" if more > 0 else ""
        raise ImportFormatError("匯入未執行，請修正後重傳：\n" + "\n".join(shown) + suffix)
    return _distinguish_repeats(transactions)


def _parse_firstrade(csv_path: Path) -> list[BrokerageTransaction]:
    return load_transaction_csv(csv_path)


FORMATS: dict[str, ImportFormat] = {
    "firstrade": ImportFormat(
        key="firstrade",
        market_code=US_MARKET.code,
        label="Firstrade",
        instructions="Firstrade 網頁版：帳戶 → 歷史紀錄 → 匯出 CSV（中英文版面皆可）。",
        parser=_parse_firstrade,
    ),
    "generic-us": ImportFormat(
        key="generic-us",
        market_code=US_MARKET.code,
        label="通用格式（美股）",
        instructions="其他美股券商：整理成範本的欄位後上傳。價格與費用以美元計。",
        parser=lambda path: _parse_generic(path, US_MARKET.code),
        template="generic-us.csv",
    ),
    "generic-tw": ImportFormat(
        key="generic-tw",
        market_code=TW_MARKET.code,
        label="通用格式（台股）",
        instructions=(
            "台股券商：整理成範本的欄位後上傳。價格與費用以台幣計；代號要寫完整（2330.TW）。"
            "賣出的證交稅填在「稅」欄；股利填在「價格」欄（整筆金額），被扣的補充保費填「稅」欄。"
        ),
        parser=lambda path: _parse_generic(path, TW_MARKET.code),
        template="generic-tw.csv",
    ),
}


def list_formats() -> list[ImportFormat]:
    return list(FORMATS.values())


def get_format(key: str | None) -> ImportFormat:
    chosen = key or DEFAULT_FORMAT_KEY
    if chosen not in FORMATS:
        raise UnknownImportFormatError(
            f"不認得的匯入格式「{chosen}」，可用：{'、'.join(FORMATS)}"
        )
    return FORMATS[chosen]


__all__ = [
    "DEFAULT_FORMAT_KEY",
    "ImportFormat",
    "ImportFormatError",
    "UnknownImportFormatError",
    "UnrecognizedCsvLayoutError",
    "get_format",
    "list_formats",
]
