from __future__ import annotations

import json
import shutil
import smtplib
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from .advisor import DISCLAIMER, FULL_FORMAT, MessageFormat, build_narrator, format_signal_card
from .alerts import TriggeredAlert
from .config import EmailSettings, MessageStyle, Settings
from .signals import Signal

OSASCRIPT = "osascript"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class DeliveryResult:
    channel: str
    delivered: bool
    detail: str = ""


class NotificationChannel(Protocol):
    name: str

    def send(self, title: str, message: str) -> DeliveryResult: ...


def _escape_for_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


@dataclass(frozen=True)
class DesktopChannel:
    """macOS notification centre via the built-in osascript."""

    name: str = "desktop"

    def send(self, title: str, message: str) -> DeliveryResult:
        if shutil.which(OSASCRIPT) is None:
            return DeliveryResult(self.name, False, "osascript 不可用（非 macOS）")
        script = (
            f'display notification "{_escape_for_applescript(message)}"'
            f' with title "{_escape_for_applescript(title)}" sound name "Ping"'
        )
        result = subprocess.run(
            [OSASCRIPT, "-e", script], capture_output=True, text=True, check=False
        )
        detail = result.stderr.strip() if result.returncode else ""
        return DeliveryResult(self.name, result.returncode == 0, detail)


@dataclass(frozen=True)
class TelegramChannel:
    """Reaches the phone even when the Mac is asleep."""

    bot_token: str
    chat_id: str
    name: str = "telegram"

    def send(self, title: str, message: str) -> DeliveryResult:
        # Plain text keeps symbols / optional generated prose from becoming markup.
        # Split on line boundaries when possible; count UTF-16 units conservatively.
        for text in _telegram_chunks(f"{title}\n{message}"):
            payload = json.dumps({"chat_id": self.chat_id, "text": text}).encode("utf-8")
            request = urllib.request.Request(
                TELEGRAM_API.format(token=self.bot_token),
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            result = self._perform(request)
            if not result.delivered:
                return result
        return DeliveryResult(self.name, True)

    def _perform(self, request: urllib.request.Request) -> DeliveryResult:
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return DeliveryResult(self.name, response.status == 200)
        except urllib.error.HTTPError as error:
            return DeliveryResult(self.name, False, f"HTTP {error.code}: {error.read()[:200]!r}")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return DeliveryResult(self.name, False, str(error))


def _telegram_chunks(text: str, limit: int = 3500) -> list[str]:
    chunks = []
    while text:
        units = 0
        end = 0
        for char in text:
            size = 2 if ord(char) > 0xFFFF else 1
            if units + size > limit:
                break
            units += size
            end += 1
        if end < len(text):
            boundary = text.rfind("\n", 0, end)
            if boundary >= 0:
                end = boundary + 1
        chunks.append(text[:end])
        text = text[end:]
    return chunks


@dataclass(frozen=True)
class EmailChannel:
    """SMTP email, so an alert reaches an inbox that outlives any one device.

    Uses only the standard library; the connection details live in EmailSettings and
    never in code (the doc's API-key-safety point)."""

    settings: EmailSettings
    name: str = "email"

    def send(self, title: str, message: str) -> DeliveryResult:
        payload = EmailMessage()
        payload["Subject"] = title
        payload["From"] = self.settings.sender
        payload["To"] = self.settings.recipient
        payload.set_content(message)
        try:
            self._deliver(payload)
        except (smtplib.SMTPException, OSError) as error:
            return DeliveryResult(self.name, False, str(error))
        return DeliveryResult(self.name, True)

    def _deliver(self, payload: EmailMessage) -> None:
        with smtplib.SMTP(
            self.settings.smtp_host, self.settings.smtp_port, timeout=REQUEST_TIMEOUT_SECONDS
        ) as smtp:
            if self.settings.use_tls:
                smtp.starttls()
            if self.settings.username:
                smtp.login(self.settings.username, self.settings.password)
            smtp.send_message(payload)


def build_channels(settings: Settings) -> list[NotificationChannel]:
    channels: list[NotificationChannel] = []
    if settings.desktop_notifications:
        channels.append(DesktopChannel())
    if settings.telegram.is_configured:
        channels.append(
            TelegramChannel(
                bot_token=settings.telegram.bot_token, chat_id=settings.telegram.chat_id
            )
        )
    if settings.email.is_configured:
        channels.append(EmailChannel(settings.email))
    return channels


def format_alert_message(alerts: list[TriggeredAlert]) -> tuple[str, str]:
    title = f"美股價格提醒（{len(alerts)} 檔）"
    body = "\n".join(f"• {alert.headline}" for alert in alerts)
    return title, body


def format_signal_message(
    signals: list[Signal], narrator=None, message_format: MessageFormat = FULL_FORMAT
) -> tuple[str, str]:
    title = f"美股智慧提醒（{len(signals)} 檔）"
    separator = "\n" if message_format.style is MessageStyle.COMPACT else "\n\n"
    body = separator.join(format_signal_card(s, narrator, message_format) for s in signals)
    if signals:
        body += f"\n\n提醒：{DISCLAIMER}"
    return title, body


def dispatch_alerts(settings: Settings, alerts: list[TriggeredAlert]) -> list[DeliveryResult]:
    if not alerts:
        return []
    title, body = format_alert_message(alerts)
    return [channel.send(title, body) for channel in build_channels(settings)]


def dispatch_signals(settings: Settings, signals: list[Signal]) -> list[DeliveryResult]:
    """Each channel gets its own style; messages are built once per style so an LLM
    narrator is not called again for every channel."""
    if not signals:
        return []
    narrator = build_narrator(settings)
    messages: dict[MessageFormat, tuple[str, str]] = {}
    results = []
    for channel in build_channels(settings):
        message_format = message_format_for(settings, channel.name)
        if message_format not in messages:
            messages[message_format] = format_signal_message(signals, narrator, message_format)
        results.append(channel.send(*messages[message_format]))
    return results


def message_format_for(settings: Settings, channel_name: str) -> MessageFormat:
    preferences = settings.messages
    return MessageFormat(preferences.style_for(channel_name), preferences.show_holdings)


def any_delivered(results: list[DeliveryResult]) -> bool:
    return any(result.delivered for result in results)
