from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .market import MarketDataClient
from .service import run_alert_check, run_signal_check

LOGGER = logging.getLogger(__name__)
SECONDS_PER_MINUTE = 60


async def _run_one_check(settings: Settings, client: MarketDataClient) -> None:
    outcome = await asyncio.to_thread(run_alert_check, settings, client)
    if outcome.triggered:
        LOGGER.info("觸發 %d 筆價格提醒，送達=%s", len(outcome.triggered), outcome.notified)
    for channel in outcome.failed_channels:
        LOGGER.warning("通知管道 %s 發送失敗", channel)
    await _run_signal_check(settings, client)


async def _run_signal_check(settings: Settings, client: MarketDataClient) -> None:
    signal_outcome = await asyncio.to_thread(run_signal_check, settings, client)
    if signal_outcome.fresh:
        LOGGER.info("智慧提醒新觸發 %d 檔，送達=%s", len(signal_outcome.fresh), signal_outcome.notified)
    for channel in signal_outcome.failed_channels:
        LOGGER.warning("通知管道 %s 發送失敗", channel)
    for item in signal_outcome.stale:
        LOGGER.warning("行情資料過期，略過 %s：%s", item.symbol, item.describe_age)


async def periodic_alert_check(settings: Settings, client: MarketDataClient) -> None:
    """Replaces launchd when the server runs somewhere without a local cron."""
    interval = settings.check_interval_minutes * SECONDS_PER_MINUTE
    while True:
        await asyncio.sleep(interval)
        try:
            await _run_one_check(settings, client)
        except Exception:  # noqa: BLE001 - a bad tick must not kill the loop
            LOGGER.exception("背景價格檢查失敗，將於下次週期重試")
