from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd

from .firstrade_csv import BrokerageTransaction
from .holdings import ManualPosition, Position
from .performance import PortfolioPerformance

INSERT_TRANSACTION = """
    INSERT OR IGNORE INTO transactions (
        trade_date, settled_date, symbol, activity, quantity, price,
        cash_amount, commission, fee, description, withheld_amount, fx_rate, row_fingerprint
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

SELECT_TRANSACTIONS = """
    SELECT trade_date, settled_date, symbol, activity, quantity, price,
           cash_amount, commission, fee, description, withheld_amount, fx_rate
    FROM transactions ORDER BY trade_date, id
"""


def _as_insert_row(transaction: BrokerageTransaction) -> tuple:
    return (
        transaction.trade_date.isoformat(),
        transaction.settled_date.isoformat() if transaction.settled_date else None,
        transaction.symbol,
        transaction.activity,
        transaction.quantity,
        transaction.price,
        transaction.cash_amount,
        transaction.commission,
        transaction.fee,
        transaction.description,
        transaction.withheld_amount,
        transaction.fx_rate,
        transaction.row_fingerprint,
    )


def save_transactions(
    connection: sqlite3.Connection, transactions: list[BrokerageTransaction]
) -> int:
    """Returns the number of genuinely new rows; re-importing a CSV is a no-op."""
    before = connection.total_changes
    connection.executemany(INSERT_TRANSACTION, [_as_insert_row(t) for t in transactions])
    connection.commit()
    return connection.total_changes - before


def load_transactions(connection: sqlite3.Connection) -> list[BrokerageTransaction]:
    from .firstrade_csv import parse_date

    rows = connection.execute(SELECT_TRANSACTIONS).fetchall()
    return [
        BrokerageTransaction(
            trade_date=parse_date(row["trade_date"]),  # type: ignore[arg-type]
            settled_date=parse_date(row["settled_date"] or ""),
            symbol=row["symbol"],
            activity=row["activity"],
            quantity=row["quantity"],
            price=row["price"],
            cash_amount=row["cash_amount"],
            commission=row["commission"],
            fee=row["fee"],
            description=row["description"] or "",
            withheld_amount=row["withheld_amount"],
            fx_rate=row["fx_rate"],
        )
        for row in rows
    ]


def save_position_snapshot(
    connection: sqlite3.Connection,
    performance: PortfolioPerformance,
    snapshot_date: date,
    cash_balance: float = 0.0,
) -> None:
    rows = [
        (
            snapshot_date.isoformat(),
            position.symbol,
            position.quantity,
            position.last_price,
            position.market_value,
            position.average_cost,
        )
        for position in performance.positions
    ]
    connection.executemany(
        "INSERT OR REPLACE INTO position_snapshots VALUES (?, ?, ?, ?, ?, ?)", rows
    )
    _save_account_snapshot(connection, performance, snapshot_date, cash_balance)
    connection.commit()


def _save_account_snapshot(
    connection: sqlite3.Connection,
    performance: PortfolioPerformance,
    snapshot_date: date,
    cash_balance: float,
) -> None:
    equity_value = performance.total_market_value
    connection.execute(
        "INSERT OR REPLACE INTO account_snapshots VALUES (?, ?, ?, ?)",
        (
            snapshot_date.isoformat(),
            equity_value,
            cash_balance,
            equity_value + cash_balance,
        ),
    )


def load_cash_balance_on(connection: sqlite3.Connection, snapshot_date: date) -> float | None:
    """None means no snapshot exists for that day yet, which is not the same as zero
    cash — the daily scheduler must not overwrite a hand-entered balance with 0."""
    row = connection.execute(
        "SELECT cash_balance FROM account_snapshots WHERE snapshot_date = ?",
        (snapshot_date.isoformat(),),
    ).fetchone()
    return None if row is None else float(row[0])


def load_account_value_history(connection: sqlite3.Connection) -> list[tuple[str, float]]:
    rows = connection.execute(
        "SELECT snapshot_date, total_value FROM account_snapshots ORDER BY snapshot_date"
    ).fetchall()
    return [(row["snapshot_date"], row["total_value"]) for row in rows]


def save_fx_rates(connection: sqlite3.Connection, pair: str, rates: dict[str, float]) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO fx_rates VALUES (?, ?, ?)",
        [(pair, day, rate) for day, rate in rates.items()],
    )
    connection.commit()


def load_fx_rates(connection: sqlite3.Connection, pair: str) -> dict[str, float]:
    rows = connection.execute(
        "SELECT rate_date, rate FROM fx_rates WHERE pair = ?", (pair,)
    ).fetchall()
    return {row["rate_date"]: row["rate"] for row in rows}


def update_transaction_fx_rates(
    connection: sqlite3.Connection, rates_by_fingerprint: dict[str, float]
) -> int:
    """Backfills rates onto rows imported before FX tracking existed."""
    connection.executemany(
        "UPDATE transactions SET fx_rate = ? WHERE row_fingerprint = ? AND fx_rate IS NULL",
        [(rate, fingerprint) for fingerprint, rate in rates_by_fingerprint.items()],
    )
    connection.commit()
    return len(rates_by_fingerprint)


def count_transactions_without_fx(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS missing FROM transactions WHERE fx_rate IS NULL"
    ).fetchone()
    return row["missing"]


def save_manual_position(
    connection: sqlite3.Connection, manual: "ManualPosition"
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO manual_positions VALUES (?, ?, ?, ?)",
        (manual.symbol.upper(), manual.quantity, manual.average_cost, manual.note),
    )
    connection.commit()


def load_manual_positions(connection: sqlite3.Connection) -> list[Position]:
    rows = connection.execute(
        "SELECT symbol, quantity, average_cost FROM manual_positions ORDER BY symbol"
    ).fetchall()
    return [
        Position(
            symbol=row["symbol"],
            quantity=row["quantity"],
            total_cost=row["quantity"] * row["average_cost"],
        )
        for row in rows
    ]


def load_manual_position_records(connection: sqlite3.Connection) -> list[ManualPosition]:
    """Same rows as load_manual_positions, keeping the note the UI needs."""
    rows = connection.execute(
        "SELECT symbol, quantity, average_cost, note FROM manual_positions ORDER BY symbol"
    ).fetchall()
    return [
        ManualPosition(
            symbol=row["symbol"],
            quantity=row["quantity"],
            average_cost=row["average_cost"],
            note=row["note"] or "",
        )
        for row in rows
    ]


def delete_manual_position(connection: sqlite3.Connection, symbol: str) -> bool:
    cursor = connection.execute(
        "DELETE FROM manual_positions WHERE symbol = ?", (symbol.upper(),)
    )
    connection.commit()
    return cursor.rowcount > 0


def save_alert_rule(
    connection: sqlite3.Connection,
    symbol: str,
    buy_below: float | None,
    sell_above: float | None,
) -> None:
    existing = connection.execute(
        "SELECT buy_below, sell_above FROM price_alerts WHERE symbol = ?", (symbol.upper(),)
    ).fetchone()
    merged_buy = buy_below if buy_below is not None else (existing["buy_below"] if existing else None)
    merged_sell = sell_above if sell_above is not None else (existing["sell_above"] if existing else None)
    connection.execute(
        "INSERT OR REPLACE INTO price_alerts (symbol, buy_below, sell_above, note, last_fired_date)"
        " VALUES (?, ?, ?, '', NULL)",
        (symbol.upper(), merged_buy, merged_sell),
    )
    connection.commit()


def load_alert_rules(connection: sqlite3.Connection) -> list["AlertRule"]:
    from .alerts import AlertRule

    rows = connection.execute(
        "SELECT symbol, buy_below, sell_above, last_fired_date FROM price_alerts ORDER BY symbol"
    ).fetchall()
    return [
        AlertRule(
            symbol=row["symbol"],
            buy_below=row["buy_below"],
            sell_above=row["sell_above"],
            last_fired_date=row["last_fired_date"],
        )
        for row in rows
    ]


def delete_alert_rule(connection: sqlite3.Connection, symbol: str) -> bool:
    cursor = connection.execute(
        "DELETE FROM price_alerts WHERE symbol = ?", (symbol.upper(),)
    )
    connection.commit()
    return cursor.rowcount > 0


def mark_alert_fired(connection: sqlite3.Connection, symbol: str, fired_on: date) -> None:
    connection.execute(
        "UPDATE price_alerts SET last_fired_date = ? WHERE symbol = ?",
        (fired_on.isoformat(), symbol.upper()),
    )
    connection.commit()


def load_signal_states(
    connection: sqlite3.Connection,
) -> dict[str, tuple[str | None, str | None]]:
    """symbol -> (last_fired_date, last_conditions). Backs the smart-alert same-day
    dedup, which re-arms when the set of triggered conditions changes."""
    rows = connection.execute(
        "SELECT symbol, last_fired_date, last_conditions FROM signal_alerts"
    ).fetchall()
    return {row["symbol"]: (row["last_fired_date"], row["last_conditions"]) for row in rows}


def mark_signal_fired(
    connection: sqlite3.Connection, symbol: str, fired_on: date, conditions_key: str
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO signal_alerts (symbol, last_fired_date, last_conditions)"
        " VALUES (?, ?, ?)",
        (symbol.upper(), fired_on.isoformat(), conditions_key),
    )
    connection.commit()


def save_price_history(connection: sqlite3.Connection, symbol: str, closes: "pd.Series") -> None:
    """Upserts adjusted closes so the smart-alert check does not refetch a full year of
    history on every 30-minute tick."""
    rows = [
        (symbol.upper(), pd.Timestamp(day).date().isoformat(), float(close))
        for day, close in closes.items()
        if pd.notna(close)
    ]
    if not rows:
        return
    connection.executemany(
        "INSERT OR REPLACE INTO price_history (symbol, price_date, close) VALUES (?, ?, ?)", rows
    )
    connection.commit()


def load_price_history(connection: sqlite3.Connection, symbol: str) -> "pd.Series":
    rows = connection.execute(
        "SELECT price_date, close FROM price_history WHERE symbol = ? ORDER BY price_date",
        (symbol.upper(),),
    ).fetchall()
    if not rows:
        return pd.Series(dtype="float64")
    index = pd.to_datetime([row["price_date"] for row in rows])
    return pd.Series([row["close"] for row in rows], index=index)


def price_history_fetched_on(connection: sqlite3.Connection, symbol: str) -> str | None:
    row = connection.execute(
        "SELECT fetched_on FROM price_fetch_log WHERE symbol = ?", (symbol.upper(),)
    ).fetchone()
    return row["fetched_on"] if row else None


def mark_price_history_fetched(
    connection: sqlite3.Connection, symbol: str, fetched_on: date
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO price_fetch_log (symbol, fetched_on) VALUES (?, ?)",
        (symbol.upper(), fetched_on.isoformat()),
    )
    connection.commit()


def record_signal_event(connection: sqlite3.Connection, event: "SignalEvent") -> None:
    connection.execute(
        "INSERT INTO signal_events (event_date, symbol, direction, conditions, last_price)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            event.event_date.isoformat(),
            event.symbol.upper(),
            event.direction,
            event.conditions,
            event.last_price,
        ),
    )
    connection.commit()


def load_last_signal_prices(connection: sqlite3.Connection) -> dict[str, float]:
    """symbol -> price in the most recent delivered smart alert, for the further-move rule."""
    rows = connection.execute(
        "SELECT symbol, last_price FROM signal_events"
        " WHERE id IN (SELECT MAX(id) FROM signal_events GROUP BY symbol)"
    ).fetchall()
    return {row["symbol"]: float(row["last_price"]) for row in rows}


def load_signal_events(
    connection: sqlite3.Connection, symbol: str | None = None, limit: int = 50
) -> list["SignalEventRecord"]:
    from .signals import SignalEventRecord

    query = "SELECT event_date, symbol, direction, conditions, last_price FROM signal_events"
    params: list[object] = []
    if symbol:
        query += " WHERE symbol = ?"
        params.append(symbol.upper())
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = connection.execute(query, params).fetchall()
    return [
        SignalEventRecord(
            event_date=row["event_date"],
            symbol=row["symbol"],
            direction=row["direction"],
            conditions=row["conditions"],
            last_price=row["last_price"],
        )
        for row in rows
    ]
