from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date

import pandas as pd

from .indicators import BOLLINGER_WINDOW, RSI_PERIOD
from .signals import (
    BUY,
    PositionPricing,
    PriceHistory,
    Signal,
    SignalThresholds,
    evaluate_signal_from_closes,
)

# Below this many closes no indicator can be evaluated, so there is nothing to replay.
MIN_EVALUABLE_HISTORY = max(RSI_PERIOD + 1, BOLLINGER_WINDOW)

# Trading-day horizons at which a buy signal's forward return is measured.
DEFAULT_HORIZONS = (20, 60)


@dataclass(frozen=True)
class SignalEpisode:
    """One unbroken stretch the same signal stayed triggered. Reported instead of raw
    days so a month sitting below a band counts as one event, not thirty."""

    direction: str
    start: date
    end: date
    peak_severity: int


@dataclass(frozen=True)
class ForwardReturnStats:
    """What happened after a buy signal first fired. This is the 'accuracy' half of a
    backtest: frequency says how often a rule speaks, this says whether it was right.

    Measured only at each episode's first day (the entry), never every day it stayed
    on, so a long stretch counts as one outcome. Uses adjusted closes, so a dividend
    paid inside the window is included, not read as a drop."""

    horizon_days: int
    sample_size: int
    win_rate_percent: float | None
    median_return_percent: float | None
    average_return_percent: float | None


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    evaluable_days: int
    trigger_days: int
    episodes: tuple[SignalEpisode, ...]
    forward_returns: tuple[ForwardReturnStats, ...] = ()

    @property
    def episode_count(self) -> int:
        return len(self.episodes)

    @property
    def trigger_day_ratio(self) -> float:
        """Share of evaluable days a rule was firing. Near 1.0 means the threshold is
        so loose it is always on — the signal carries no information."""
        if not self.evaluable_days:
            return 0.0
        return self.trigger_days / self.evaluable_days

    def episodes_for(self, direction: str) -> list[SignalEpisode]:
        return [episode for episode in self.episodes if episode.direction == direction]


def backtest_signals(
    history: PriceHistory,
    *,
    thresholds: SignalThresholds | None = None,
    average_cost: float | None = None,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> BacktestResult:
    """Replay the rule engine day by day over adjusted closes.

    Indicators are recomputed on the history available up to each day, so the result
    is what the engine would have said in real time — it never peeks at later prices.
    """
    prices = history.closes.dropna()
    triggered_per_day = _replay_daily_signals(history.symbol, prices, thresholds, average_cost)
    entry_positions = _buy_entry_positions(triggered_per_day, offset=MIN_EVALUABLE_HISTORY - 1)
    return BacktestResult(
        symbol=history.symbol,
        evaluable_days=len(triggered_per_day),
        trigger_days=sum(1 for signal in triggered_per_day if signal is not None),
        episodes=tuple(_collect_episodes(_evaluable_dates(prices), triggered_per_day)),
        forward_returns=tuple(
            _forward_return_stats(prices, entry_positions, horizon) for horizon in horizons
        ),
    )


def _replay_daily_signals(
    symbol: str,
    prices: pd.Series,
    thresholds: SignalThresholds | None,
    average_cost: float | None,
) -> list[Signal | None]:
    pricing = PositionPricing(average_cost=average_cost)
    return [
        evaluate_signal_from_closes(
            PriceHistory(symbol, prices.iloc[: index + 1]),
            thresholds=thresholds,
            pricing=pricing,
        )
        for index in range(MIN_EVALUABLE_HISTORY - 1, len(prices))
    ]


def _evaluable_dates(prices: pd.Series) -> list[date]:
    return [_as_date(stamp) for stamp in prices.index[MIN_EVALUABLE_HISTORY - 1 :]]


def _buy_entry_positions(signals: list[Signal | None], offset: int) -> list[int]:
    """Absolute positions in the price series where a BUY episode first begins."""
    positions: list[int] = []
    previous: str | None = None
    for local_index, signal in enumerate(signals):
        direction = signal.direction if signal else None
        if direction == BUY and previous != BUY:
            positions.append(offset + local_index)
        previous = direction
    return positions


def _forward_return_stats(
    prices: pd.Series, entry_positions: list[int], horizon: int
) -> ForwardReturnStats:
    returns = [
        (float(prices.iloc[position + horizon]) / float(prices.iloc[position]) - 1) * 100
        for position in entry_positions
        if position + horizon < len(prices) and prices.iloc[position]
    ]
    if not returns:
        return ForwardReturnStats(horizon, 0, None, None, None)
    wins = sum(1 for value in returns if value > 0)
    return ForwardReturnStats(
        horizon_days=horizon,
        sample_size=len(returns),
        win_rate_percent=wins / len(returns) * 100,
        median_return_percent=statistics.median(returns),
        average_return_percent=statistics.fmean(returns),
    )


def _collect_episodes(
    dates: list[date], signals: list[Signal | None]
) -> list[SignalEpisode]:
    episodes: list[SignalEpisode] = []
    open_start: date | None = None
    open_direction: str | None = None
    open_peak = 0
    for day, signal in zip(dates, signals):
        direction = signal.direction if signal else None
        if direction != open_direction:
            if open_direction is not None and open_start is not None:
                episodes.append(SignalEpisode(open_direction, open_start, prev_day, open_peak))
            open_direction, open_start, open_peak = direction, day, 0
        if signal is not None:
            open_peak = max(open_peak, signal.severity)
        prev_day = day
    if open_direction is not None and open_start is not None:
        episodes.append(SignalEpisode(open_direction, open_start, prev_day, open_peak))
    return episodes


def _as_date(stamp) -> date:
    # pandas Timestamp subclasses datetime (hence date), so normalise everything
    # through Timestamp to get a plain date rather than a datetime back.
    return pd.Timestamp(stamp).date()
