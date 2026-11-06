import smtplib
import urllib.error

import pytest

from portfolio.alerts import TriggeredAlert
from conftest import make_settings
from portfolio.config import EmailSettings, TelegramSettings
from portfolio.indicators import BollingerBands, IndicatorSnapshot
from portfolio.notifiers import (
    DeliveryResult,
    EmailChannel,
    TelegramChannel,
    any_delivered,
    build_channels,
    dispatch_alerts,
    dispatch_signals,
    format_alert_message,
    format_signal_message,
)
from portfolio.signals import PositionPricing, evaluate_signal

ALERT = TriggeredAlert("MSFT", "BUY", last_price=497.12, target_price=520.0)


def _signal():
    snapshot = IndicatorSnapshot(
        symbol="NVDA", last_close=85.0, rsi=25.0, sma_short=100.0, sma_long=100.0,
        bands=BollingerBands(lower=90.0, middle=105.0, upper=120.0),
    )
    return evaluate_signal(snapshot, pricing=PositionPricing(average_cost=100.0))


def _email(**kwargs) -> EmailSettings:
    base = dict(
        smtp_host="smtp.example.com", smtp_port=587, username="u", password="p",
        sender="from@example.com", recipient="to@example.com", use_tls=True,
    )
    base.update(kwargs)
    return EmailSettings(**base)


def _settings(tmp_path, *, desktop: bool, telegram: TelegramSettings, email=None):
    return make_settings(
        tmp_path, desktop_notifications=desktop, telegram=telegram, email=email
    )


def test_no_channels_when_everything_is_disabled(tmp_path):
    settings = _settings(tmp_path, desktop=False, telegram=TelegramSettings("", ""))
    assert build_channels(settings) == []


def test_desktop_channel_is_built_when_enabled(tmp_path):
    settings = _settings(tmp_path, desktop=True, telegram=TelegramSettings("", ""))
    assert [c.name for c in build_channels(settings)] == ["desktop"]


def test_telegram_needs_both_token_and_chat_id(tmp_path):
    partial = _settings(tmp_path, desktop=False, telegram=TelegramSettings("token", ""))
    assert build_channels(partial) == []


def test_both_channels_are_built_when_fully_configured(tmp_path):
    settings = _settings(tmp_path, desktop=True, telegram=TelegramSettings("token", "123"))
    assert [c.name for c in build_channels(settings)] == ["desktop", "telegram"]


def test_message_lists_every_alert_with_a_count_in_the_title():
    second = TriggeredAlert("NVDA", "SELL", last_price=300.0, target_price=280.0)
    title, body = format_alert_message([ALERT, second])
    assert "2 檔" in title
    assert "MSFT" in body and "NVDA" in body
    assert body.count("•") == 2


def test_dispatch_does_nothing_without_alerts(tmp_path):
    settings = _settings(tmp_path, desktop=True, telegram=TelegramSettings("t", "1"))
    assert dispatch_alerts(settings, []) == []


def test_telegram_reports_success_on_http_200(monkeypatch):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse())
    result = TelegramChannel("token", "123").send("t", "m")
    assert result.delivered
    assert result.channel == "telegram"


def test_telegram_reports_an_http_error_without_raising(monkeypatch):
    def raise_http_error(request, timeout):
        raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    result = TelegramChannel("bad", "123").send("t", "m")
    assert not result.delivered
    assert "401" in result.detail


def test_telegram_reports_a_network_failure_without_raising(monkeypatch):
    def raise_url_error(request, timeout):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr("urllib.request.urlopen", raise_url_error)
    result = TelegramChannel("token", "123").send("t", "m")
    assert not result.delivered
    assert "no route" in result.detail


def test_telegram_sends_the_configured_chat_id(monkeypatch):
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def capture(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data.decode("utf-8")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", capture)
    TelegramChannel("SECRET", "42").send("標題", "內容")
    assert "botSECRET/sendMessage" in captured["url"]
    assert '"chat_id": "42"' in captured["body"]


def test_any_delivered_needs_only_one_success():
    results = [DeliveryResult("desktop", False, "boom"), DeliveryResult("telegram", True)]
    assert any_delivered(results)
    assert not any_delivered([DeliveryResult("desktop", False)])
    assert not any_delivered([])


class FakeSMTP:
    """Stands in for smtplib.SMTP as a context manager, recording what it was told."""

    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.tls = False
        self.login_args = None
        self.sent = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        self.tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.sent = message


def test_email_channel_is_built_only_when_configured(tmp_path):
    off = _settings(tmp_path, desktop=False, telegram=TelegramSettings("", ""), email=_email(smtp_host=""))
    assert build_channels(off) == []
    on = _settings(tmp_path, desktop=False, telegram=TelegramSettings("", ""), email=_email())
    assert [c.name for c in build_channels(on)] == ["email"]


def test_all_three_channels_come_in_a_stable_order(tmp_path):
    settings = _settings(
        tmp_path, desktop=True, telegram=TelegramSettings("t", "1"), email=_email()
    )
    assert [c.name for c in build_channels(settings)] == ["desktop", "telegram", "email"]


def test_email_send_uses_tls_login_and_the_configured_recipient(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    result = EmailChannel(_email()).send("標題", "內容")
    assert result.delivered
    sent = FakeSMTP.instances[0]
    assert sent.tls is True
    assert sent.login_args == ("u", "p")
    assert sent.sent["To"] == "to@example.com"
    assert sent.sent["Subject"] == "標題"


def test_email_skips_login_when_no_username(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    EmailChannel(_email(username="")).send("t", "m")
    assert FakeSMTP.instances[0].login_args is None


def test_email_reports_failure_without_raising(monkeypatch):
    def boom(*args, **kwargs):
        raise smtplib.SMTPException("relay refused")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    result = EmailChannel(_email()).send("t", "m")
    assert not result.delivered
    assert "relay refused" in result.detail


def test_signal_message_lists_each_symbol_and_the_disclaimer():
    title, body = format_signal_message([_signal()])
    assert "1 檔" in title
    assert "NVDA" in body
    assert "非投資建議" in body


def test_dispatch_signals_does_nothing_without_signals(tmp_path):
    settings = _settings(tmp_path, desktop=True, telegram=TelegramSettings("t", "1"))
    assert dispatch_signals(settings, []) == []


def test_batch_disclaimer_once_and_llm_cannot_remove_factual_details():
    from dataclasses import replace
    from portfolio.advisor import DISCLAIMER, LLMNarrator
    first = _signal()
    second = replace(first, symbol="MSFT")
    _, body = format_signal_message([first, second], LLMNarrator(lambda s, u: "近期走勢偏弱。"))
    assert body.count(DISCLAIMER) == 1
    assert body.count("摘要：近期走勢偏弱。") == 2
    assert body.count("低於設定門檻 30") == 2
    assert body.count("指標資料截至：") == 2


def test_price_alert_includes_distance_from_target():
    _, body = format_alert_message([ALERT])
    assert "低於目標價 4.4%" in body


def test_telegram_splits_long_unicode_without_losing_text_or_interpreting_markup(monkeypatch):
    import json
    captured = []
    def capture(self, request):
        captured.append(json.loads(request.data))
        return DeliveryResult("telegram", True)
    monkeypatch.setattr(TelegramChannel, "_perform", capture)
    body = ("NVDA_[test] 😀\n" * 700) + "尾端"
    result = TelegramChannel("token", "42").send("提醒", body)
    assert result.delivered
    assert len(captured) > 1
    assert "".join(p["text"] for p in captured) == "提醒\n" + body
    assert all(len(p["text"].encode("utf-16-le")) // 2 <= 3500 for p in captured)
    assert all("parse_mode" not in p for p in captured)


def test_telegram_partial_delivery_does_not_report_success(monkeypatch):
    results = iter([DeliveryResult("telegram", True), DeliveryResult("telegram", False, "offline")])
    monkeypatch.setattr(TelegramChannel, "_perform", lambda self, request: next(results))
    result = TelegramChannel("token", "42").send("提醒", "x" * 5000)
    assert not result.delivered
    assert result.detail == "offline"


def test_each_channel_receives_its_own_message_style(tmp_path, monkeypatch):
    from dataclasses import replace

    from portfolio.config import MessagePreferences, MessageStyle

    sent: dict[str, str] = {}

    def record(self, title, message):
        sent[self.name] = message
        return DeliveryResult(self.name, True)

    monkeypatch.setattr("portfolio.notifiers.DesktopChannel.send", record)
    monkeypatch.setattr("portfolio.notifiers.TelegramChannel.send", record)
    monkeypatch.setattr("portfolio.notifiers.EmailChannel.send", record)
    settings = replace(
        _settings(tmp_path, desktop=True, telegram=TelegramSettings("t", "1"), email=_email()),
        messages=MessagePreferences(style=MessageStyle.PLAIN, email_style=MessageStyle.FULL),
    )
    dispatch_signals(settings, [_signal()])
    assert sent["desktop"].splitlines()[0].startswith("📉 NVDA USD $85.00｜")
    assert "設定門檻" not in sent["telegram"]
    assert "設定門檻" in sent["email"]


def test_unknown_message_style_falls_back_to_plain(tmp_path, monkeypatch):
    from portfolio.config import MessageStyle, Settings

    monkeypatch.setattr("portfolio.config.ENV_FILE", tmp_path / "absent.env")  # keep a local .env out
    monkeypatch.setenv("PORTFOLIO_MESSAGE_STYLE", "fancy")
    monkeypatch.setenv("PORTFOLIO_TELEGRAM_MESSAGE_STYLE", "COMPACT")
    messages = Settings.from_environment().messages
    assert messages.style is MessageStyle.PLAIN
    assert messages.style_for("telegram") is MessageStyle.COMPACT
