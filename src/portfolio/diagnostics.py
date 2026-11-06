from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .firstrade_csv import BrokerageTransaction
from .holdings import build_ledger

RECORD_DATE_SHARES_PATTERN = re.compile(r"ON\s+([\d,.]+)\s+SHS", re.IGNORECASE)
RECORD_DATE_PATTERN = re.compile(r"REC\s+(\d{2})/(\d{2})/(\d{2})", re.IGNORECASE)
SHARE_TOLERANCE = 0.01
TWENTY_FIRST_CENTURY = 2000


@dataclass(frozen=True)
class MissingHistoryWarning:
    """A dividend proves more shares were held than the imported buys account for."""

    symbol: str
    shares_implied_by_dividend: float
    shares_from_imported_trades: float

    @property
    def missing_shares(self) -> float:
        return self.shares_implied_by_dividend - self.shares_from_imported_trades


def extract_record_date_shares(description: str) -> float | None:
    match = RECORD_DATE_SHARES_PATTERN.search(description)
    if match is None:
        return None
    return float(match.group(1).replace(",", ""))


def extract_record_date(description: str) -> date | None:
    match = RECORD_DATE_PATTERN.search(description)
    if match is None:
        return None
    month, day, year = (int(part) for part in match.groups())
    return date(TWENTY_FIRST_CENTURY + year, month, day)


@dataclass(frozen=True)
class DividendShareProof:
    """A dividend states the shares held on its record date — the one hard share count
    a statement gives for holdings bought before the export window."""

    record_date: date | None
    shares: float


def _share_proofs(transactions: list[BrokerageTransaction]) -> dict[str, list[DividendShareProof]]:
    proofs: dict[str, list[DividendShareProof]] = {}
    for transaction in transactions:
        if transaction.activity != "DIVIDEND":
            continue
        shares = extract_record_date_shares(transaction.description)
        if shares is None:
            continue
        # A statement without a REC date still proves the share count; it just cannot
        # say when, so that proof falls back to comparing against every known trade.
        record_date = extract_record_date(transaction.description)
        proofs.setdefault(transaction.symbol, []).append(DividendShareProof(record_date, shares))
    return proofs


def _shares_acquired_by(
    transactions: list[BrokerageTransaction], symbol: str, cutoff: date | None
) -> float:
    """Only trades settled by the record date counted toward that dividend, so a
    reinvestment that landed afterwards must not be subtracted from it."""
    ledger = build_ledger(
        [
            transaction
            for transaction in transactions
            if transaction.symbol == symbol and (cutoff is None or transaction.trade_date <= cutoff)
        ]
    )
    position = ledger.positions.get(symbol)
    return position.quantity if position else 0.0


def _largest_gap_for(
    transactions: list[BrokerageTransaction], symbol: str, proofs: list[DividendShareProof]
) -> MissingHistoryWarning | None:
    gaps = [
        (proof.shares - _shares_acquired_by(transactions, symbol, proof.record_date), proof)
        for proof in proofs
    ]
    missing, proof = max(gaps, key=lambda pair: pair[0])
    if missing <= SHARE_TOLERANCE:
        return None
    return MissingHistoryWarning(symbol, proof.shares, proof.shares - missing)


def detect_missing_cost_basis(
    transactions: list[BrokerageTransaction],
) -> list[MissingHistoryWarning]:
    """Compares each dividend against the shares known at *its own* record date. Using
    today's holdings instead double-subtracts reinvestments that settled later."""
    found = (
        _largest_gap_for(transactions, symbol, proofs)
        for symbol, proofs in _share_proofs(transactions).items()
    )
    warnings = [warning for warning in found if warning is not None]
    return sorted(warnings, key=lambda warning: -warning.missing_shares)
