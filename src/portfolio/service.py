from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .alerts import AlertStatus, TriggeredAlert, build_statuses, evaluate_rules
from .assumptions import AssumptionOverride, resolve_assumptions
from .config import Settings
from .currency import pair_symbol
from .diagnostics import MissingHistoryWarning, detect_missing_cost_basis
from .dividends import DividendReceipt, ForwardDividendForecast, forecast_dividends, summarize_received_dividends
from .firstrade_csv import BrokerageTransaction, load_transaction_csv
from .holdings import Position, PositionLedger, build_ledger
from .indicators import SHORT_TREND_WINDOW, compute_indicators, moving_average_series
from .market import MarketDataClient
from .market_calendar import is_close_series_current
from .markets import (
    US_MARKET,
    Market,
    is_taiwan_symbol,
    market_for_symbol,
    resolve_symbol_input,
)
from .renotify import LastNotification, decide_renotification
from .renotify import signal_conditions_key as _conditions_key
from .notifiers import DeliveryResult, any_delivered, dispatch_alerts, dispatch_signals
from .performance import (
    ExchangeRateUnavailableError,
    PortfolioPerformance,
    build_position_performance,
)
from .repository import (
    load_alert_rules,
    load_last_signal_prices,
    load_manual_positions,
    load_price_history,
    load_signal_states,
    load_transactions,
    mark_alert_fired,
    mark_price_history_fetched,
    mark_signal_fired,
    record_signal_event,
    save_price_history,
)
from .signal_config import resolve_thresholds
from .signal_subscriptions import (
    DefaultScope,
    SignalSubscription,
    load_subscription_overrides,
    normalize_symbol,
    resolve_subscriptions,
    store_subscription_choice,
)
from .signals import (
    MarketContext,
    PositionPricing,
    Signal,
    SignalEvent,
    SignalCondition,
    SignalThresholds,
    StaleSignalData,
    evaluate_signal,
    is_signal_data_stale,
    signal_data_age_days,
)
from .store import open_store
from .target_pe_options import TargetPeChoices, TargetPeContext, build_target_pe_choices
from .valuation import ValuationAssumptions

SOURCE_COMMAND_LINE = "命令列"

# A "recent" broad market move is judged over the last week of trading.
MARKET_MOVE_SESSIONS = 5


class NoPositionsError(RuntimeError):
    """Raised when an operation needs holdings but none have been entered."""


@dataclass(frozen=True)
class ImportOutcome:
    parsed: int
    inserted: int
    activity_breakdown: dict[str, int]
    warnings: list[MissingHistoryWarning]


@dataclass(frozen=True)
class DividendOverview:
    receipts: dict[str, DividendReceipt]
    forecasts: list[ForwardDividendForecast]
    withholding_rate: float


def load_positions(settings: Settings) -> tuple[list[Position], float]:
    """CSV-derived positions plus manually entered ones; manual entry wins on conflict."""
    ordered, ledger = _load_positions_and_ledger(settings)
    return ordered, ledger.total_realized_gain()


def _load_positions_and_ledger(settings: Settings) -> tuple[list[Position], PositionLedger]:
    with open_store(settings.database_path) as connection:
        transactions = load_transactions(connection)
        manual = load_manual_positions(connection)
    ledger = build_ledger(transactions)
    merged = _positions_with_proven_shares(ledger, transactions)
    merged.update({position.symbol: position for position in manual})
    ordered = sorted(merged.values(), key=lambda position: position.symbol)
    return ordered, ledger


def _positions_with_proven_shares(
    ledger: PositionLedger, transactions: list[BrokerageTransaction]
) -> dict[str, Position]:
    """A dividend proves shares the import never saw a purchase for. Counting them keeps
    market value and weights right; their cost stays unknown rather than estimated.

    A symbol whose whole holding predates the window has no open ledger position at all,
    so it must be created here or it would be missing from the portfolio entirely."""
    positions = {position.symbol: position for position in ledger.open_positions()}
    for warning in detect_missing_cost_basis(transactions):
        existing = positions.get(warning.symbol) or Position(symbol=warning.symbol)
        positions[warning.symbol] = dataclasses.replace(
            existing, unpriced_quantity=warning.missing_shares
        )
    return positions


def load_performance(settings: Settings, client: MarketDataClient) -> PortfolioPerformance:
    positions, ledger = _load_positions_and_ledger(settings)
    if not positions:
        raise NoPositionsError("沒有任何持倉")
    quotes = client.quotes([position.symbol for position in positions])
    rate_now = _current_home_rate(settings, client)
    symbols = [position.symbol for position in positions] + list(ledger.positions)
    usd_to_twd = _usd_to_twd_rate(settings, client, symbols, rate_now)
    performances = [
        build_position_performance(
            position,
            quotes[position.symbol],
            client.fundamentals(position.symbol).text("sector"),
            rate_now,
            usd_to_twd,
            settings.home_currency,
        )
        for position in positions
    ]
    realized_gain = _realized_gain_usd(ledger, usd_to_twd)
    return PortfolioPerformance(positions=performances, realized_gain=realized_gain)


def _realized_gain_usd(ledger: PositionLedger, usd_to_twd: float | None) -> float:
    us_gain, tw_gain = ledger.realized_gain_by_market()
    if not tw_gain:
        return us_gain
    if not usd_to_twd:
        raise ExchangeRateUnavailableError("已實現損益含台股，需要 USD/TWD 匯率才能合計")
    return us_gain + tw_gain / usd_to_twd


def _usd_to_twd_rate(
    settings: Settings,
    client: MarketDataClient,
    symbols: list[str],
    home_rate: float | None,
) -> float | None:
    """Only fetched when a Taiwan symbol needs it, so US-only portfolios make no extra
    call. Reuses the home-currency rate when home is TWD."""
    if not any(is_taiwan_symbol(symbol) for symbol in symbols):
        return None
    if settings.home_currency.upper() == "TWD" and home_rate:
        return home_rate
    try:
        return client.latest_exchange_rate(pair_symbol("TWD"))
    except Exception:  # noqa: BLE001 - surfaced as a clear error by the caller
        return None


def _current_home_rate(settings: Settings, client: MarketDataClient) -> float | None:
    """A missing rate degrades to USD-only output rather than failing the whole view."""
    try:
        return client.latest_exchange_rate(pair_symbol(settings.home_currency))
    except Exception:  # noqa: BLE001 - FX is supplementary, never fatal
        return None


def load_target_pe_choices(
    settings: Settings, client: MarketDataClient, symbol: str
) -> TargetPeChoices:
    """The selectable multiples for one symbol, each previewed through the full
    valuation report so the user picks against outcomes, not against a bare number."""
    ticker = symbol.upper()
    quote = client.quote(ticker)
    with open_store(settings.database_path) as connection:
        entry = resolve_assumptions(connection, client, ticker)
    context = TargetPeContext(
        symbol=ticker,
        last_price=quote.last_price,
        fundamentals=client.fundamentals(ticker),
        assumptions=entry.assumptions,
    )
    return build_target_pe_choices(context, entry.historical)


def resolve_for_symbols(
    settings: Settings,
    client: MarketDataClient,
    symbols: list[str],
    adhoc: AssumptionOverride | None = None,
) -> tuple[dict[str, ValuationAssumptions], dict[str, tuple[str, float | None]]]:
    """Stored per-symbol assumptions, with any ad-hoc override layered on top."""
    resolved: dict[str, ValuationAssumptions] = {}
    provenance: dict[str, tuple[str, float | None]] = {}
    uses_adhoc = adhoc is not None and not adhoc.is_empty()
    with open_store(settings.database_path) as connection:
        for symbol in symbols:
            entry = resolve_assumptions(connection, client, symbol)
            resolved[symbol] = adhoc.apply_to(entry.assumptions) if uses_adhoc else entry.assumptions
            provenance[symbol] = (
                SOURCE_COMMAND_LINE if uses_adhoc else entry.source,
                entry.historical_median_pe,
            )
    return resolved, provenance


def default_target_symbols(settings: Settings, positions: list[Position]) -> list[str]:
    with open_store(settings.database_path) as connection:
        watched = [rule.symbol for rule in load_alert_rules(connection)]
    return sorted({position.symbol for position in positions} | set(watched))


def import_transactions(
    settings: Settings, csv_path: Path, client: MarketDataClient | None = None
) -> ImportOutcome:
    from .fx_sync import backfill_transaction_fx
    from .repository import save_transactions

    transactions = load_transaction_csv(csv_path)
    with open_store(settings.database_path) as connection:
        inserted = save_transactions(connection, transactions)
    if client is not None:
        backfill_transaction_fx(settings, client)
    return ImportOutcome(
        parsed=len(transactions),
        inserted=inserted,
        activity_breakdown=_count_activities(transactions),
        warnings=detect_missing_cost_basis(transactions),
    )


def _count_activities(transactions: list[BrokerageTransaction]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for transaction in transactions:
        counts[transaction.activity] = counts.get(transaction.activity, 0) + 1
    return dict(sorted(counts.items()))


def load_dividend_overview(settings: Settings, client: MarketDataClient) -> DividendOverview:
    with open_store(settings.database_path) as connection:
        transactions = load_transactions(connection)
    positions, _ = load_positions(settings)
    return DividendOverview(
        receipts=summarize_received_dividends(transactions),
        forecasts=forecast_dividends(client, positions, settings.dividend_withholding_rate),
        withholding_rate=settings.dividend_withholding_rate,
    )


def load_watchlist(settings: Settings, client: MarketDataClient) -> list[AlertStatus]:
    with open_store(settings.database_path) as connection:
        rules = load_alert_rules(connection)
    if not rules:
        return []
    quotes = client.quotes([rule.symbol for rule in rules])
    return build_statuses(rules, quotes)


@dataclass(frozen=True)
class AlertCheckOutcome:
    triggered: list[TriggeredAlert]
    deliveries: list[DeliveryResult]

    @property
    def notified(self) -> bool:
        return any_delivered(self.deliveries)

    @property
    def failed_channels(self) -> list[str]:
        return [d.channel for d in self.deliveries if not d.delivered]


def run_alert_check(
    settings: Settings, client: MarketDataClient, notify: bool = True, resend: bool = False
) -> AlertCheckOutcome:
    with open_store(settings.database_path) as connection:
        rules = [rule for rule in load_alert_rules(connection) if rule.has_target]
    if not rules:
        return AlertCheckOutcome([], [])
    quotes = client.quotes([rule.symbol for rule in rules])
    triggered = evaluate_rules(rules, quotes, date.today(), resend_same_day=resend)
    deliveries = dispatch_alerts(settings, triggered) if notify else []
    _record_fired(settings, triggered)
    return AlertCheckOutcome(triggered, deliveries)


def _record_fired(settings: Settings, triggered: list[TriggeredAlert]) -> None:
    if not triggered:
        return
    with open_store(settings.database_path) as connection:
        for alert in triggered:
            mark_alert_fired(connection, alert.symbol, date.today())


def _fetch_adjusted_closes(
    settings: Settings, client: MarketDataClient, symbol: str, lookback_days: int
) -> pd.Series:
    """Adjusted closes (ex-dividend/splits are not read as drops), cached in the DB so a
    30-minute schedule does not refetch a year of history every tick.

    The gate asks what the cache *contains*, not when it was last written. The old
    once-a-day marker froze the series for 24 hours whenever the first fetch of the UTC
    day landed before yfinance published that session's bar — and a run of failures then
    kept it frozen for three weeks, feeding stale prices into every indicator."""
    today = date.today()
    end = today + timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    now = datetime.now(timezone.utc)
    with open_store(settings.database_path) as connection:
        cached = load_price_history(connection, symbol)
        if is_close_series_current(_last_close_day(cached), now, market_for_symbol(symbol)):
            return cached
        fetched = _download_closes(client, symbol, start, end)
        if fetched.empty:
            return cached
        save_price_history(connection, symbol, fetched)
        mark_price_history_fetched(connection, symbol, today)
        return fetched


def _last_close_day(closes: pd.Series) -> date | None:
    if closes.empty or not isinstance(closes.index, pd.DatetimeIndex):
        return None
    return closes.index[-1].date()


def _download_closes(
    client: MarketDataClient, symbol: str, start: date, end: date
) -> pd.Series:
    """An empty series on failure lets the stale cache stand in rather than dropping the
    symbol — a day-old line still beats no signal."""
    try:
        return client.close_prices(symbol, start, end)
    except Exception:  # noqa: BLE001 - fall back to whatever we cached before
        return pd.Series(dtype="float64")


def benchmark_symbol_for(settings: Settings, market: Market) -> str:
    return settings.benchmark_symbol if market is US_MARKET else settings.tw_benchmark_symbol


def market_context(
    settings: Settings, client: MarketDataClient, market: Market = US_MARKET
) -> MarketContext | None:
    """The benchmark's own recent move, so a stock-specific rule can be told apart
    from the whole market sliding. Each market is compared with its own index."""
    benchmark = benchmark_symbol_for(settings, market)
    closes = _fetch_adjusted_closes(settings, client, benchmark, settings.signal_lookback_days)
    closes = closes.dropna()
    if len(closes) < 2:
        return None
    reference = closes.iloc[-(MARKET_MOVE_SESSIONS + 1)] if len(closes) > MARKET_MOVE_SESSIONS else closes.iloc[0]
    if not reference:
        return None
    change = (float(closes.iloc[-1]) / float(reference) - 1) * 100
    sessions = min(MARKET_MOVE_SESSIONS, len(closes) - 1)
    dated = isinstance(closes.index, pd.DatetimeIndex)
    return MarketContext(
        benchmark_symbol=benchmark, change_percent=change, sessions=sessions,
        start_date=closes.index[-sessions - 1].date().isoformat() if dated else None,
        end_date=closes.index[-1].date().isoformat() if dated else None,
    )


@dataclass(frozen=True)
class SignalScan:
    """What one pass over the data found: what triggered, and what could not be judged
    because its closes were too old. An empty `signals` alone cannot tell those apart."""

    signals: list[Signal]
    stale: list[StaleSignalData]


def scan_signals(
    settings: Settings,
    client: MarketDataClient,
    scope: SignalScope | None = None,
) -> SignalScan:
    """Run the rule engine over holdings + watchlist (or a given list). Strongest
    signal (most conditions) first. Never returns a recommendation, only observations.

    A scope with thresholds applies them to every symbol (an ad-hoc run); otherwise
    each symbol resolves its own stored thresholds, falling back to the global default.
    """
    requested = scope or SignalScope()
    positions, _ = load_positions(settings)
    subscriptions = _subscriptions_for(settings, positions)
    tickers = requested.symbols or [item.symbol for item in subscriptions]
    if not tickers:
        return SignalScan(signals=[], stale=[])
    tuning_by_symbol = _resolve_tuning_for(settings, client, tickers, requested.thresholds)
    scans = [
        _scan_symbol(settings, client, target, tuning_by_symbol[target.symbol])
        for target in _targets_for(tickers, positions)
    ]
    muted = _muted_symbols(settings)
    signals = [
        dataclasses.replace(scan.signal, is_muted=scan.signal.symbol in muted)
        for scan in scans
        if scan.signal is not None
    ]
    return SignalScan(
        signals=sorted(signals, key=lambda signal: signal.severity, reverse=True),
        stale=[scan.stale for scan in scans if scan.stale is not None],
    )


def load_signal_subscriptions(settings: Settings) -> list[SignalSubscription]:
    """Holdings + watchlist are in by default; stored rows mute or add symbols."""
    positions, _ = load_positions(settings)
    return _subscriptions_for(settings, positions)


def resolve_symbol(client: MarketDataClient | None, raw_symbol: str) -> str:
    """User-typed symbol -> stored symbol; `2330` becomes `2330.TW` or `2330.TWO`."""
    lookup = None if client is None else (lambda symbol: _has_price(client, symbol))
    return resolve_symbol_input(raw_symbol, lookup)


def _has_price(client: MarketDataClient, symbol: str) -> bool:
    try:
        return client.quote(symbol).last_price > 0
    except Exception:  # noqa: BLE001 - an unknown ticker surfaces as any transport error
        return False


def set_signal_subscription(
    settings: Settings, raw_symbol: str, enabled: bool, client: MarketDataClient | None = None
) -> SignalSubscription:
    symbol = normalize_symbol(resolve_symbol(client, raw_symbol))
    positions, _ = load_positions(settings)
    with open_store(settings.database_path) as connection:
        store_subscription_choice(connection, _default_signal_scope(connection, positions), symbol, enabled)
    current = {item.symbol: item for item in _subscriptions_for(settings, positions)}
    return current.get(symbol, SignalSubscription(symbol, (), False))


def _subscriptions_for(settings: Settings, positions: list[Position]) -> list[SignalSubscription]:
    with open_store(settings.database_path) as connection:
        scope = _default_signal_scope(connection, positions)
        overrides = load_subscription_overrides(connection)
    return resolve_subscriptions(scope, overrides)


def _default_signal_scope(connection, positions: list[Position]) -> DefaultScope:
    watched = frozenset(rule.symbol for rule in load_alert_rules(connection))
    return DefaultScope(held=frozenset(p.symbol for p in positions), watched=watched)


def _muted_symbols(settings: Settings) -> set[str]:
    with open_store(settings.database_path) as connection:
        overrides = load_subscription_overrides(connection)
    return {symbol for symbol, enabled in overrides.items() if not enabled}


def evaluate_signals(
    settings: Settings, client: MarketDataClient, scope: SignalScope | None = None
) -> list[Signal]:
    """Signals only. Callers that must tell a quiet market from a broken feed need
    scan_signals instead — an empty list here means nothing it can distinguish."""
    return scan_signals(settings, client, scope).signals


def _targets_for(symbols: list[str], positions: list[Position]) -> list[SignalTarget]:
    average_costs = {position.symbol: position.average_cost for position in positions}
    return [SignalTarget(symbol, average_costs.get(symbol)) for symbol in symbols]


def _resolve_tuning_for(
    settings: Settings,
    client: MarketDataClient,
    symbols: list[str],
    override: SignalThresholds | None,
) -> dict[str, SignalTuning]:
    thresholds = _resolve_thresholds_for(settings, symbols, override)
    markets = {market_for_symbol(symbol) for symbol in symbols}
    contexts = {market: market_context(settings, client, market) for market in markets}
    return {
        symbol: SignalTuning(thresholds[symbol], contexts[market_for_symbol(symbol)])
        for symbol in symbols
    }


def _resolve_thresholds_for(
    settings: Settings, symbols: list[str], override: SignalThresholds | None
) -> dict[str, SignalThresholds]:
    if override is not None:
        return {symbol: override for symbol in symbols}
    with open_store(settings.database_path) as connection:
        return {symbol: resolve_thresholds(connection, symbol).thresholds for symbol in symbols}


@dataclass(frozen=True)
class SymbolScan:
    """Exactly one of these is set: a symbol either yields a signal (or no trigger) or
    is reported stale. Both would mean claiming a reading the data cannot support."""

    signal: Signal | None = None
    stale: StaleSignalData | None = None


def _scan_symbol(
    settings: Settings, client: MarketDataClient, target: SignalTarget, tuning: SignalTuning
) -> SymbolScan:
    """Stale closes produce no signal at all. A live quote cannot rescue them: every
    band and average comes from the close series, so the condition itself would be
    describing some earlier week. Staleness is reported, never silently dropped."""
    symbol = target.symbol
    closes = _fetch_adjusted_closes(settings, client, symbol, settings.signal_lookback_days)
    snapshot = compute_indicators(symbol, closes)
    as_of = snapshot.as_of if snapshot else None
    if snapshot is None or is_signal_data_stale(as_of, date.today()):
        age = signal_data_age_days(as_of, date.today())
        return SymbolScan(stale=StaleSignalData(symbol, as_of, age))
    signal = evaluate_signal(
        snapshot,
        thresholds=tuning.thresholds,
        pricing=_signal_pricing(client, symbol, target.average_cost),
        market_context=tuning.market_context,
    )
    if signal is not None:
        reference, buy_below, basis = _valuation_hint(settings, client, symbol, target.average_cost)
        signal = dataclasses.replace(
            signal, reference_price=reference, buy_below_price=buy_below, valuation_basis=basis
        )
    return SymbolScan(signal=signal)


def _valuation_hint(
    settings: Settings, client: MarketDataClient, symbol: str, average_cost: float | None
) -> tuple[float | None, float | None, str | None]:
    """The 入手價 view's reference fair value and buy-below price, best-effort. Any
    failure (no fundamentals, a flaky lookup) just omits the line — it never blocks the
    alert. Mechanical model output, like everywhere else: context, not advice."""
    from .assumptions import load_global_default, load_override
    from .targets import build_entry_target

    try:
        # Default + any stored per-symbol override only — NOT the 5-year historical-P/E
        # derivation, so an alert cycle stays light (no extra multi-year fetch). The full
        # 入手價 view may differ where it uses a symbol's historical P/E.
        with open_store(settings.database_path) as connection:
            assumptions = load_global_default(connection)
            override = load_override(connection, symbol)
        if override is not None and not override.is_empty():
            assumptions = override.apply_to(assumptions)
        target = build_entry_target(client, symbol, assumptions, average_cost)
        if target.reference_price is None:
            return None, None, None
        return target.reference_price, target.buy_below, target.basis
    except Exception:  # noqa: BLE001 - valuation is supplementary, never fatal to an alert
        return None, None, None


def _signal_pricing(client: MarketDataClient, symbol: str, average_cost: float | None) -> PositionPricing:
    try:
        quote = client.quote(symbol)
        if quote.last_price > 0:
            return PositionPricing(
                last_price=quote.last_price, average_cost=average_cost,
                retrieved_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                currency=quote.currency,
                daily_change_percent=quote.daily_change_percent,
            )
    except Exception:  # noqa: BLE001 - preserve the history-only fallback
        pass
    return PositionPricing(average_cost=average_cost)


def signal_conditions_key(signal: Signal) -> str:
    """Stable identity for a signal's rule set; a changed set re-arms the alert."""
    return _conditions_key(signal)


@dataclass(frozen=True)
class SignalTarget:
    """要評估的標的與我在它上面的成本。成本找不到時是 None，但一定跟著它的代號走。"""

    symbol: str
    average_cost: float | None = None


@dataclass(frozen=True)
class SignalTuning:
    """一次評估共用的門檻與大盤背景，每檔都要同一組，分開傳容易漏掉其中一個。"""

    thresholds: SignalThresholds
    market_context: MarketContext | None = None


@dataclass(frozen=True)
class SignalScope:
    """要評估哪些標的、用什麼門檻。None 代表「全部持倉」與「預設門檻」。"""

    symbols: list[str] | None = None
    thresholds: SignalThresholds | None = None


@dataclass(frozen=True)
class DispatchOptions:
    """notify 與 resend 都是 bool 且相鄰，分開傳叫錯順序不會編譯錯。"""

    notify: bool = True
    resend: bool = False


@dataclass(frozen=True)
class SignalCheckOutcome:
    signals: list[Signal]
    fresh: list[Signal]
    deliveries: list[DeliveryResult]
    notify_requested: bool = True
    stale: list[StaleSignalData] = field(default_factory=list)
    """Symbols skipped for old data. Without this, a broken feed is indistinguishable
    from a calm market: both show an empty signal list."""

    @property
    def notified(self) -> bool:
        return any_delivered(self.deliveries)

    @property
    def failed_channels(self) -> list[str]:
        return [d.channel for d in self.deliveries if not d.delivered]


def run_signal_check(
    settings: Settings,
    client: MarketDataClient,
    scope: SignalScope | None = None,
    options: DispatchOptions | None = None,
) -> SignalCheckOutcome:
    """Evaluate signals and notify only new or meaningfully changed ones, so a
    30-minute schedule does not spam an unchanged condition."""
    scope = scope or SignalScope()
    options = options or DispatchOptions()
    scan = scan_signals(settings, client, scope)
    signals = scan.signals
    candidates = _notifiable_signals(settings, signals)
    fresh = _select_signals_to_notify(settings, candidates, resend=options.resend)
    deliveries = dispatch_signals(settings, fresh) if options.notify else []
    if _should_suppress_until_tomorrow(options, deliveries):
        _record_signals_fired(settings, fresh)
    return SignalCheckOutcome(
        signals=signals,
        fresh=fresh,
        deliveries=deliveries,
        notify_requested=options.notify,
        stale=scan.stale,
    )


def _should_suppress_until_tomorrow(
    options: DispatchOptions, deliveries: list[DeliveryResult]
) -> bool:
    """Only a confirmed delivery earns the rest of the day off: marking an undelivered
    signal makes the next 30-minute tick skip it, silently swallowing the day's alert."""
    return options.notify and any_delivered(deliveries)


def _notifiable_signals(settings: Settings, signals: list[Signal]) -> list[Signal]:
    """Muted symbols, and with PORTFOLIO_MUTE_MARKET_WIDE the signals a broad market move
    explains, still appear in the returned list but are not pushed."""
    audible = [signal for signal in signals if not signal.is_muted]
    if not settings.mute_market_wide_signals:
        return audible
    return [signal for signal in audible if not signal.likely_market_wide]


def _select_signals_to_notify(
    settings: Settings, signals: list[Signal], *, resend: bool = False
) -> list[Signal]:
    """An unchanged signal is re-sent only after a further move or a quiet spell
    (renotify.py), so a stock parked under its band does not repeat every morning."""
    history = _load_last_notifications(settings)
    today = date.today()
    fresh = []
    for signal in signals:
        decision = decide_renotification(signal, history.get(signal.symbol), settings.renotify, today)
        if not (decision.should_notify or resend):
            continue
        note = f"手動重送｜{decision.note}" if resend else decision.note
        fresh.append(dataclasses.replace(signal, notification_note=note))
    return fresh


def _load_last_notifications(settings: Settings) -> dict[str, LastNotification]:
    with open_store(settings.database_path) as connection:
        states = load_signal_states(connection)
        prices = load_last_signal_prices(connection)
    return {
        symbol: LastNotification(date.fromisoformat(fired_date), key, prices.get(symbol))
        for symbol, (fired_date, key) in states.items()
        if fired_date and key
    }


def _record_signals_fired(settings: Settings, signals: list[Signal]) -> None:
    if not signals:
        return
    today = date.today()
    with open_store(settings.database_path) as connection:
        for signal in signals:
            mark_signal_fired(connection, signal.symbol, today, signal_conditions_key(signal))
            record_signal_event(connection, _as_signal_event(signal, today))


def _as_signal_event(signal: Signal, event_date: date) -> SignalEvent:
    return SignalEvent(
        event_date=event_date,
        symbol=signal.symbol,
        direction=signal.direction,
        conditions=signal_conditions_key(signal),
        last_price=signal.last_price,
    )


def load_signal_history(settings: Settings, symbol: str | None = None, limit: int = 50):
    """The log of signals that actually fired, newest first. A read-through for CLI and
    the web so callers do not touch the store directly."""
    from .repository import load_signal_events

    with open_store(settings.database_path) as connection:
        return load_signal_events(connection, symbol, limit)


@dataclass(frozen=True)
class ClosePoint:
    """One drawable day: the stored close plus the short-trend line at that date."""

    price_date: str
    close: float
    moving_average: float | None


def load_close_history(settings: Settings, symbol: str, lookback_days: int) -> list[ClosePoint]:
    """Cached closes only — drawing a chart must never trigger a network fetch."""
    with open_store(settings.database_path) as connection:
        closes = load_price_history(connection, symbol)
    return _as_close_points(closes.dropna(), max(1, lookback_days))


def _as_close_points(closes: pd.Series, lookback_days: int) -> list[ClosePoint]:
    averages = moving_average_series(closes, SHORT_TREND_WINDOW)
    return [
        ClosePoint(
            price_date=pd.Timestamp(day).date().isoformat(),
            close=float(close),
            moving_average=_drawable_average(averages.get(day)),
        )
        for day, close in _within_lookback(closes, lookback_days).items()
    ]


def _within_lookback(closes: pd.Series, lookback_days: int) -> pd.Series:
    """Cut from the newest stored date, not today, so a stale cache still fills the window."""
    if closes.empty:
        return closes
    return closes[closes.index > closes.index[-1] - pd.Timedelta(days=lookback_days)]


def _drawable_average(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)
