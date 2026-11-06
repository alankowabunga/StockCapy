from datetime import date

import pytest

from portfolio.config import EmailSettings, Settings, TelegramSettings
from portfolio.firstrade_csv import BrokerageTransaction
from portfolio.market import Fundamentals


def make_settings(
    data_dir,
    *,
    api_token: str = "",
    desktop_notifications: bool = False,
    telegram: TelegramSettings | None = None,
    email: EmailSettings | None = None,
) -> Settings:
    """One place to build Settings so a new field does not break every test file."""
    return Settings(
        data_dir=data_dir,
        database_path=data_dir / "test.sqlite3",
        home_currency="TWD",
        dividend_withholding_rate=0.30,
        api_token=api_token,
        telegram=telegram or TelegramSettings(bot_token="", chat_id=""),
        email=email or EmailSettings("", 587, "", "", "", "", use_tls=True),
        desktop_notifications=desktop_notifications,
        check_interval_minutes=0,
        benchmark_symbol="SPY",
        signal_lookback_days=400,
        mute_market_wide_signals=False,
        llm_narration=False,
        llm_model="claude-opus-5",
    )


def transaction(
    *,
    symbol: str = "AAPL",
    activity: str = "BUY",
    quantity: float = 10.0,
    price: float = 100.0,
    cash_amount: float = -1000.0,
    trade_day: int = 1,
    commission: float = 0.0,
    fee: float = 0.0,
    description: str = "",
    fx_rate: float | None = None,
    trade_date: date | None = None,
) -> BrokerageTransaction:
    return BrokerageTransaction(
        trade_date=trade_date or date(2026, 1, trade_day),
        settled_date=None,
        symbol=symbol,
        activity=activity,
        quantity=quantity,
        price=price,
        cash_amount=cash_amount,
        commission=commission,
        fee=fee,
        description=description,
        fx_rate=fx_rate,
    )


@pytest.fixture
def dividend_payer() -> Fundamentals:
    return Fundamentals(
        symbol="KO",
        values={
            "trailingEps": 2.5,
            "forwardEps": 2.8,
            "bookValue": 6.0,
            "trailingPE": 20.0,
            "dividendRate": 2.0,
            "dividendYield": 3.0,
            "targetMeanPrice": 70.0,
            "numberOfAnalystOpinions": 22,
            "sector": "Consumer Defensive",
        },
    )


@pytest.fixture
def expensive_growth_stock() -> Fundamentals:
    return Fundamentals(
        symbol="NVDA",
        values={
            "trailingEps": 3.0,
            "forwardEps": 4.0,
            "bookValue": 2.0,
            "trailingPE": 60.0,
            "dividendRate": 0.04,
            "dividendYield": 0.03,
            "targetMeanPrice": 200.0,
            "numberOfAnalystOpinions": 40,
            "sector": "Technology",
        },
    )
