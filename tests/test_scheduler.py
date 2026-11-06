import asyncio

import pytest
from conftest import make_settings

from portfolio.scheduler import periodic_alert_check



def test_periodic_check_runs_repeatedly_and_survives_failures(tmp_path, monkeypatch):
    """A failing tick must not kill the loop, or alerts stop silently forever."""
    settings = make_settings(tmp_path)
    object.__setattr__(settings, "check_interval_minutes", 1)
    calls = []

    def fake_check(_settings, _client):
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("行情暫時取不到")
        return _Outcome()

    monkeypatch.setattr("portfolio.scheduler.run_alert_check", fake_check)
    monkeypatch.setattr("portfolio.scheduler.asyncio.sleep", _instant_sleep(limit=3))

    with pytest.raises(_StopLoop):
        asyncio.run(periodic_alert_check(settings, client=None))
    assert len(calls) == 3


def test_periodic_check_waits_before_the_first_run(tmp_path, monkeypatch):
    """Sleeping first avoids a burst of notifications every time the server restarts."""
    settings = make_settings(tmp_path)
    object.__setattr__(settings, "check_interval_minutes", 5)
    order = []

    monkeypatch.setattr(
        "portfolio.scheduler.run_alert_check",
        lambda _s, _c: order.append("check") or _Outcome(),
    )

    async def record_sleep(seconds):
        order.append(f"sleep:{seconds}")
        if order.count("check") >= 1:
            raise _StopLoop

    monkeypatch.setattr("portfolio.scheduler.asyncio.sleep", record_sleep)
    with pytest.raises(_StopLoop):
        asyncio.run(periodic_alert_check(settings, client=None))
    assert order[0] == "sleep:300"


class _StopLoop(Exception):
    pass


class _Outcome:
    triggered: list = []
    notified = False
    failed_channels: list = []


def _instant_sleep(limit: int):
    state = {"ticks": 0}

    async def sleep(_seconds):
        state["ticks"] += 1
        if state["ticks"] > limit:
            raise _StopLoop

    return sleep
