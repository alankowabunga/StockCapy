from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

COLUMN_ALIASES = {
    "symbol": ("symbol", "ticker", "代號", "代号"),
    "quantity": ("quantity", "qty", "shares", "數量", "数量"),
    "price": ("price", "unitprice", "價格", "价格"),
    "activity": ("action", "activity", "transactiontype", "type", "recordtype", "交易類別", "交易类别"),
    "description": ("description", "securitydescription", "memo", "說明", "说明"),
    "trade_date": ("tradedate", "date", "transactiondate", "rundate", "日期"),
    "settled_date": ("settleddate", "settlementdate", "settledate", "交割日"),
    "cash_amount": ("amount", "netamount", "cashamount", "金額", "金额"),
    "commission": ("commission", "手續費"),
    "fee": ("fee", "fees", "secfee", "費用"),
}

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%Y/%m/%d")

# Firstrade's Chinese export names the activity directly; "其他" is a catch-all
# whose real meaning only appears in the description text.
CHINESE_ACTIVITY_MAP = {
    "買進": "BUY",
    "买进": "BUY",
    "賣出": "SELL",
    "卖出": "SELL",
    "股息": "DIVIDEND",
    "利息收入": "INTEREST",
    "存款": "TRANSFER",
    "提款": "TRANSFER",
    "電匯": "TRANSFER",
}

DESCRIPTION_PATTERNS = (
    ("BUY", (r"REIN\s*@", r"\bBUY\b", r"BOUGHT", r"PURCHASE", r"REINVEST")),
    ("WITHHOLDING", (r"TAX\s*WITHHELD", r"WITHHOLD")),
    ("DIVIDEND", (r"CASH\s*DIV", r"\bDIV\b", r"DISTRIBUTION")),
    ("INTEREST", (r"INTEREST",)),
    ("SELL", (r"\bSELL\b", r"SOLD", r"REDEMPTION")),
    ("SPLIT", (r"SPLIT",)),
    ("FEE", (r"\bFEE\b", r"COMMISSION", r"ADR\s*FEE")),
    ("TRANSFER", (r"XFER", r"TRANSFER", r"\bACH\b", r"WIRE", r"DEPOSIT", r"WITHDRAWAL")),
)

REINVESTMENT_PRICE_PATTERN = re.compile(r"REIN\s*@\s*([\d.]+)", re.IGNORECASE)
WITHHOLDING_PATTERNS = (
    re.compile(r"NON-RES\s*TAX\s*WITHHELD\s*\$?([\d,.]+)", re.IGNORECASE),
    re.compile(r"NON-QUALIFIED\s*DIVIDEND\s*\$?([\d,.]+)", re.IGNORECASE),
    re.compile(r"TAX\s*WITHHELD\s*\$?([\d,.]+)", re.IGNORECASE),
)


@dataclass(frozen=True)
class BrokerageTransaction:
    trade_date: date
    settled_date: date | None
    symbol: str
    activity: str
    quantity: float
    price: float
    cash_amount: float
    commission: float
    fee: float
    description: str
    withheld_amount: float = 0.0
    fx_rate: float | None = None

    @property
    def row_fingerprint(self) -> str:
        parts = (
            self.trade_date.isoformat(),
            self.symbol,
            self.activity,
            f"{self.quantity:.6f}",
            f"{self.price:.6f}",
            f"{self.cash_amount:.6f}",
            self.description,
        )
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class UnrecognizedCsvLayoutError(ValueError):
    """Raised when a CSV has no column resembling a trade date or symbol."""


def _normalize_header(header: str) -> str:
    stripped = header.strip().lower()
    return re.sub(r"[^a-z0-9一-鿿]", "", stripped)


def build_column_map(headers: list[str]) -> dict[str, str]:
    normalized = {_normalize_header(header): header for header in headers}
    column_map: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                column_map[field] = normalized[alias]
                break
    return column_map


def parse_date(raw_value: str) -> date | None:
    candidate = (raw_value or "").strip()
    if not candidate:
        return None
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(candidate, date_format).date()
        except ValueError:
            continue
    return None


def parse_amount(raw_value: str) -> float:
    candidate = re.sub(r"[$,\s]", "", (raw_value or "").strip())
    if not candidate or candidate in {"-", "--"}:
        return 0.0
    negative = candidate.startswith("(") and candidate.endswith(")")
    candidate = candidate.strip("()")
    try:
        parsed = float(candidate)
    except ValueError:
        return 0.0
    return -parsed if negative else parsed


def classify_activity(activity_text: str, description: str) -> str:
    direct = CHINESE_ACTIVITY_MAP.get(activity_text.strip())
    if direct:
        return direct
    haystack = f"{activity_text} {description}".upper()
    for activity, patterns in DESCRIPTION_PATTERNS:
        if any(re.search(pattern, haystack) for pattern in patterns):
            return activity
    return "OTHER"


def extract_withheld_amount(description: str) -> float:
    """Firstrade reports NRA withholding inside the dividend row's text, not as its own row."""
    for pattern in WITHHOLDING_PATTERNS:
        match = pattern.search(description)
        if match:
            return parse_amount(match.group(1))
    return 0.0


def extract_reinvestment_price(description: str) -> float:
    match = REINVESTMENT_PRICE_PATTERN.search(description)
    return float(match.group(1)) if match else 0.0


def _read_row_value(row: dict[str, str], column_map: dict[str, str], field: str) -> str:
    column = column_map.get(field)
    return row.get(column, "") if column else ""


def _resolve_price(raw_price: float, description: str, quantity: float, cash_amount: float) -> float:
    if raw_price:
        return abs(raw_price)
    reinvestment_price = extract_reinvestment_price(description)
    if reinvestment_price:
        return reinvestment_price
    return abs(cash_amount) / abs(quantity) if abs(quantity) > 1e-9 else 0.0


def _build_transaction(
    row: dict[str, str], column_map: dict[str, str]
) -> BrokerageTransaction | None:
    read = lambda field: _read_row_value(row, column_map, field)  # noqa: E731
    trade_date = parse_date(read("trade_date"))
    if trade_date is None:
        return None
    description = read("description").strip()
    quantity = parse_amount(read("quantity"))
    cash_amount = parse_amount(read("cash_amount"))
    return BrokerageTransaction(
        trade_date=trade_date,
        settled_date=parse_date(read("settled_date")),
        symbol=read("symbol").strip().upper(),
        activity=classify_activity(read("activity"), description),
        quantity=quantity,
        price=_resolve_price(parse_amount(read("price")), description, quantity, cash_amount),
        cash_amount=cash_amount,
        commission=parse_amount(read("commission")),
        fee=parse_amount(read("fee")),
        description=description,
        withheld_amount=extract_withheld_amount(description),
    )


def parse_transaction_rows(
    rows: list[dict[str, str]], headers: list[str]
) -> list[BrokerageTransaction]:
    column_map = build_column_map(headers)
    if "trade_date" not in column_map or "symbol" not in column_map:
        raise UnrecognizedCsvLayoutError(
            f"CSV lacks a recognizable date/symbol column; saw headers: {headers}"
        )
    parsed = (_build_transaction(row, column_map) for row in rows)
    return [transaction for transaction in parsed if transaction is not None]


def load_transaction_csv(csv_path: Path) -> list[BrokerageTransaction]:
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        return parse_transaction_rows(list(reader), headers)
