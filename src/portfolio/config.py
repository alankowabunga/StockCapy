from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
ENV_FILE = PROJECT_ROOT / ".env"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TOKEN_BYTES = 32


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    chat_id: str

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass(frozen=True)
class EmailSettings:
    smtp_host: str
    smtp_port: int
    username: str
    password: str
    sender: str
    recipient: str
    use_tls: bool

    @property
    def is_configured(self) -> bool:
        """Host, sender and recipient are the minimum; a relay may need no auth."""
        return bool(self.smtp_host and self.sender and self.recipient)


class MessageStyle(StrEnum):
    COMPACT = "compact"
    PLAIN = "plain"
    FULL = "full"


@dataclass(frozen=True)
class RenotifyPolicy:
    """An unchanged smart alert is re-sent only after a further move or a quiet spell."""

    further_move_percent: float = 5.0
    reminder_days: int = 7


@dataclass(frozen=True)
class MessagePreferences:
    style: MessageStyle = MessageStyle.PLAIN
    telegram_style: MessageStyle | None = None
    email_style: MessageStyle | None = None
    show_holdings: bool = True

    def style_for(self, channel_name: str) -> MessageStyle:
        """A desktop banner only shows a line or two, so it is always compact."""
        if channel_name == "desktop":
            return MessageStyle.COMPACT
        override = {"telegram": self.telegram_style, "email": self.email_style}.get(channel_name)
        return override or self.style


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    home_currency: str
    dividend_withholding_rate: float
    api_token: str
    telegram: TelegramSettings
    email: EmailSettings
    desktop_notifications: bool
    check_interval_minutes: int
    benchmark_symbol: str
    signal_lookback_days: int
    mute_market_wide_signals: bool
    llm_narration: bool
    llm_model: str
    renotify: RenotifyPolicy = field(default_factory=RenotifyPolicy)
    messages: MessagePreferences = field(default_factory=MessagePreferences)

    @classmethod
    def from_environment(cls) -> "Settings":
        load_env_file()
        data_dir = Path(os.environ.get("PORTFOLIO_DATA_DIR", DEFAULT_DATA_DIR))
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "portfolio.sqlite3",
            home_currency=os.environ.get("PORTFOLIO_HOME_CURRENCY", "TWD"),
            dividend_withholding_rate=float(os.environ.get("PORTFOLIO_NRA_WITHHOLDING", "0.30")),
            api_token=os.environ.get("PORTFOLIO_API_TOKEN", "").strip(),
            telegram=_telegram_from_environment(),
            email=_email_from_environment(),
            desktop_notifications=_flag("PORTFOLIO_DESKTOP_NOTIFICATIONS", default=True),
            check_interval_minutes=_positive_int("PORTFOLIO_CHECK_INTERVAL_MINUTES"),
            benchmark_symbol=os.environ.get("PORTFOLIO_BENCHMARK_SYMBOL", "SPY").strip() or "SPY",
            signal_lookback_days=_positive_int("PORTFOLIO_SIGNAL_LOOKBACK_DAYS") or 400,
            mute_market_wide_signals=_flag("PORTFOLIO_MUTE_MARKET_WIDE", default=False),
            llm_narration=_flag("PORTFOLIO_LLM_NARRATION", default=False),
            llm_model=os.environ.get("PORTFOLIO_LLM_MODEL", "claude-opus-5").strip() or "claude-opus-5",
            renotify=_renotify_from_environment(),
            messages=_messages_from_environment(),
        )

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir

    @property
    def requires_authentication(self) -> bool:
        return bool(self.api_token)


def load_env_file(path: Path | None = None) -> None:
    """Reads KEY=VALUE lines; real environment variables always win."""
    env_path = path or ENV_FILE
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, value = _parse_env_line(line)
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_env_line(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return "", ""
    key, _, value = stripped.partition("=")
    return key.strip(), value.strip().strip("\"'")


def _telegram_from_environment() -> TelegramSettings:
    return TelegramSettings(
        bot_token=os.environ.get("PORTFOLIO_TELEGRAM_BOT_TOKEN", "").strip(),
        chat_id=os.environ.get("PORTFOLIO_TELEGRAM_CHAT_ID", "").strip(),
    )


def _email_from_environment() -> EmailSettings:
    return EmailSettings(
        smtp_host=os.environ.get("PORTFOLIO_SMTP_HOST", "").strip(),
        smtp_port=_positive_int("PORTFOLIO_SMTP_PORT") or 587,
        username=os.environ.get("PORTFOLIO_SMTP_USERNAME", "").strip(),
        password=os.environ.get("PORTFOLIO_SMTP_PASSWORD", ""),
        sender=os.environ.get("PORTFOLIO_EMAIL_SENDER", "").strip(),
        recipient=os.environ.get("PORTFOLIO_EMAIL_RECIPIENT", "").strip(),
        use_tls=_flag("PORTFOLIO_SMTP_USE_TLS", default=True),
    )


def _renotify_from_environment() -> RenotifyPolicy:
    default = RenotifyPolicy()
    return RenotifyPolicy(
        further_move_percent=_positive_float(
            "PORTFOLIO_SIGNAL_RENOTIFY_MOVE_PERCENT", default.further_move_percent
        ),
        reminder_days=_positive_int("PORTFOLIO_SIGNAL_REMINDER_DAYS") or default.reminder_days,
    )


def _messages_from_environment() -> MessagePreferences:
    return MessagePreferences(
        style=_message_style("PORTFOLIO_MESSAGE_STYLE") or MessageStyle.PLAIN,
        telegram_style=_message_style("PORTFOLIO_TELEGRAM_MESSAGE_STYLE"),
        email_style=_message_style("PORTFOLIO_EMAIL_MESSAGE_STYLE"),
        show_holdings=_flag("PORTFOLIO_MESSAGE_SHOW_HOLDINGS", default=True),
    )


def _message_style(name: str) -> MessageStyle | None:
    """An unknown value falls back rather than crash the scheduler at boot."""
    raw = os.environ.get(name, "").strip().lower()
    return MessageStyle(raw) if raw in set(MessageStyle) else None


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_int(name: str) -> int:
    """0 means "no background checking"; used when a host has no cron of its own."""
    try:
        return max(0, int(os.environ.get(name, "0")))
    except ValueError:
        return 0


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def is_local_host(host: str) -> bool:
    return host in LOCAL_HOSTS


def generate_api_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)
