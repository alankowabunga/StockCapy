from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS transactions (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date     TEXT NOT NULL,
        settled_date   TEXT,
        symbol         TEXT NOT NULL,
        activity       TEXT NOT NULL,
        quantity       REAL NOT NULL DEFAULT 0,
        price          REAL NOT NULL DEFAULT 0,
        cash_amount    REAL NOT NULL DEFAULT 0,
        commission     REAL NOT NULL DEFAULT 0,
        fee            REAL NOT NULL DEFAULT 0,
        description    TEXT,
        withheld_amount REAL NOT NULL DEFAULT 0,
        row_fingerprint TEXT NOT NULL UNIQUE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transactions_symbol ON transactions(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(trade_date)",
    """
    CREATE TABLE IF NOT EXISTS position_snapshots (
        snapshot_date TEXT NOT NULL,
        symbol        TEXT NOT NULL,
        quantity      REAL NOT NULL,
        last_price    REAL NOT NULL,
        market_value  REAL NOT NULL,
        average_cost  REAL NOT NULL,
        PRIMARY KEY (snapshot_date, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_snapshots (
        snapshot_date TEXT PRIMARY KEY,
        equity_value  REAL NOT NULL,
        cash_balance  REAL NOT NULL,
        total_value   REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manual_positions (
        symbol       TEXT PRIMARY KEY,
        quantity     REAL NOT NULL,
        average_cost REAL NOT NULL,
        note         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_alerts (
        symbol          TEXT PRIMARY KEY,
        buy_below       REAL,
        sell_above      REAL,
        note            TEXT,
        last_fired_date TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fx_rates (
        pair      TEXT NOT NULL,
        rate_date TEXT NOT NULL,
        rate      REAL NOT NULL,
        PRIMARY KEY (pair, rate_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS valuation_assumptions (
        symbol           TEXT PRIMARY KEY,
        target_pe        REAL,
        required_return  REAL,
        dividend_growth  REAL,
        margin_of_safety REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_pe_cache (
        symbol             TEXT PRIMARY KEY,
        median_pe          REAL,
        lower_quartile_pe  REAL,
        upper_quartile_pe  REAL,
        observation_days   INTEGER NOT NULL,
        computed_on        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_history (
        symbol     TEXT NOT NULL,
        price_date TEXT NOT NULL,
        close      REAL NOT NULL,
        PRIMARY KEY (symbol, price_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_alerts (
        symbol          TEXT PRIMARY KEY,
        last_fired_date TEXT,
        last_conditions TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_thresholds (
        symbol                        TEXT PRIMARY KEY,
        average_cost_discount_percent REAL,
        short_trend_bias_percent      REAL,
        rsi_oversold                  REAL,
        rsi_overbought                REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        event_date TEXT NOT NULL,
        symbol     TEXT NOT NULL,
        direction  TEXT NOT NULL,
        conditions TEXT NOT NULL,
        last_price REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_signal_events_symbol ON signal_events(symbol)",
    """
    CREATE TABLE IF NOT EXISTS price_fetch_log (
        symbol      TEXT PRIMARY KEY,
        fetched_on  TEXT NOT NULL
    )
    """,
)


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


ADDED_COLUMNS = (
    ("transactions", "withheld_amount", "REAL NOT NULL DEFAULT 0"),
    ("transactions", "fx_rate", "REAL"),
)


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def apply_migrations(connection: sqlite3.Connection) -> None:
    """Keeps databases created by an earlier schema usable without a re-import."""
    for table, column, definition in ADDED_COLUMNS:
        if column not in _column_names(connection, table):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def create_schema(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)
    apply_migrations(connection)
    connection.commit()


@contextmanager
def open_store(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(database_path)
    try:
        create_schema(connection)
        yield connection
        connection.commit()
    finally:
        connection.close()
