from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import report, service
from .alerts import build_statuses, evaluate_rules
from .backtest import backtest_signals
from .signals import PriceHistory
from .signal_config import (
    GLOBAL_DEFAULT_KEY as SIGNAL_GLOBAL_KEY,
    ThresholdOverride,
    delete_threshold_override,
    load_all_threshold_overrides,
    load_global_thresholds,
    resolve_thresholds,
    save_threshold_override,
)
from .assumptions import (
    AssumptionOverride,
    GLOBAL_DEFAULT_KEY,
    delete_override,
    load_all_overrides,
    load_global_default,
    resolve_assumptions,
    save_override,
)
from .auth import InsecureExposureError, require_secure_binding
from .currency import format_money
from .markets import InvalidSymbolError, currency_for_symbol
from .config import Settings, generate_api_token
from .fx_sync import backfill_transaction_fx
from .diagnostics import detect_missing_cost_basis
from .dividends import forecast_dividends, summarize_received_dividends
from .firstrade_csv import UnrecognizedCsvLayoutError, load_transaction_csv
from .holdings import ManualPosition, Position, build_ledger
from .market import MarketDataClient
from .notifiers import build_channels
from .performance import (
    PortfolioPerformance,
    build_position_performance,
    compute_risk_metrics,
)
from .repository import (
    delete_alert_rule,
    delete_manual_position,
    load_account_value_history,
    load_cash_balance_on,
    load_alert_rules,
    load_manual_positions,
    load_transactions,
    mark_alert_fired,
    save_alert_rule,
    save_manual_position,
    save_position_snapshot,
    save_transactions,
)
from .store import open_store
from .targets import build_entry_targets
from .valuation import ValuationAssumptions, build_valuation_report

app = typer.Typer(
    help=(
        "StockCapy 🐹 美股合理價、入手價與到價提醒。\n\n"
        "資料來源：券商 CSV 匯出 + 公開行情。所有資料留在本機。"
    ),
    no_args_is_help=True,
)
console = Console()


def _settings() -> Settings:
    settings = Settings.from_environment()
    settings.ensure_data_dir()
    return settings


def _load_positions() -> tuple[list[Position], float]:
    return service.load_positions(_settings())


def _load_performance(client: MarketDataClient) -> PortfolioPerformance:
    try:
        return service.load_performance(_settings(), client)
    except service.NoPositionsError as error:
        console.print(
            "[yellow]沒有任何持倉。用 `portfolio import <csv>` 匯入，"
            "或 `portfolio add-position <SYM> --shares N --avg-cost X` 手動輸入。[/yellow]"
        )
        raise typer.Exit(code=1) from error
    except service.ExchangeRateUnavailableError as error:
        console.print(f"[red]{error}[/red]。請稍後再試。")
        raise typer.Exit(code=1) from error


def _resolve_for_symbols(
    client: MarketDataClient, symbols: list[str], adhoc: AssumptionOverride
) -> tuple[dict[str, ValuationAssumptions], dict[str, tuple[str, float | None]]]:
    return service.resolve_for_symbols(_settings(), client, symbols, adhoc)


def _adhoc_override(
    target_pe: float | None, required_return: float | None, margin_of_safety: float | None
) -> AssumptionOverride:
    return AssumptionOverride(
        symbol=GLOBAL_DEFAULT_KEY,
        target_pe=target_pe,
        required_return=required_return,
        margin_of_safety=margin_of_safety,
    )


@app.command("import")
def import_csv(
    csv_path: Annotated[Path, typer.Argument(help="Firstrade 匯出的交易紀錄 CSV")],
) -> None:
    """匯入 Firstrade 交易紀錄 CSV（可重複匯入，重疊資料會自動去重）。"""
    try:
        outcome = service.import_transactions(_settings(), csv_path, MarketDataClient())
    except UnrecognizedCsvLayoutError as error:
        console.print(f"[red]無法識別這個 CSV 的欄位：[/red] {error}")
        raise typer.Exit(code=1) from error
    console.print(
        f"[green]解析 {outcome.parsed} 筆、新增 {outcome.inserted} 筆[/green]"
        f"（{outcome.parsed - outcome.inserted} 筆為重複，已略過）"
    )
    summary = "  ".join(f"{name}={count}" for name, count in outcome.activity_breakdown.items())
    console.print(f"[dim]分類結果：{summary}[/dim]")
    _warn_about_missing_history(outcome.warnings)


def _warn_about_missing_history(warnings: list) -> None:
    """A dividend on more shares than the buys explain means the export starts too late."""
    for warning in warnings:
        console.print(
            f"[yellow]⚠ {warning.symbol}：股息紀錄顯示曾持有 "
            f"{warning.shares_implied_by_dividend:,.5f} 股，但匯入的買進只算出 "
            f"{warning.shares_from_imported_trades:,.5f} 股。[/yellow]"
        )
        console.print(
            f"[yellow]  缺少 {warning.missing_shares:,.5f} 股的買進紀錄（成本基礎不完整）。"
            f"請補匯入更早的區間，或用 "
            f"`portfolio add-position {warning.symbol} --shares <總股數> --avg-cost <均價>` "
            f"手動補上。[/yellow]"
        )


@app.command("unclassified")
def show_unclassified(limit: int = 20) -> None:
    """列出分類為 OTHER 的交易，用來檢查解析規則有沒有漏。"""
    settings = _settings()
    with open_store(settings.database_path) as connection:
        transactions = load_transactions(connection)
    others = [t for t in transactions if t.activity == "OTHER"][:limit]
    if not others:
        console.print("[green]沒有未分類的交易。[/green]")
        return
    for transaction in others:
        console.print(
            f"{transaction.trade_date}  {transaction.symbol or '-':6}  "
            f"${transaction.cash_amount:>10,.2f}  {transaction.description}"
        )


@app.command("show")
def show_portfolio(
    home_currency: Annotated[bool, typer.Option(help="同時顯示本國貨幣損益歸因")] = True,
) -> None:
    """顯示持倉、平均成本、現價、未實現損益與產業曝險。"""
    settings = _settings()
    performance = _load_performance(MarketDataClient())
    report.render_portfolio_summary(console, performance)
    report.render_holdings(console, performance)
    report.render_sector_exposure(console, performance)
    if home_currency:
        report.render_currency_attribution(console, performance, settings.home_currency)


@app.command("risk")
def show_risk(lookback_days: int = 365) -> None:
    """計算目前持倉的年化波動度、最大回撤、Sharpe 與 SPY 相關性。"""
    client = MarketDataClient()
    performance = _load_performance(client)
    metrics = compute_risk_metrics(client, performance, lookback_days)
    report.render_risk_metrics(console, metrics)


@app.command("value")
def value_symbol(
    symbol: str,
    target_pe: Annotated[float | None, typer.Option(help="臨時覆寫 target P/E")] = None,
    required_return: Annotated[float | None, typer.Option(help="臨時覆寫要求報酬率")] = None,
    margin_of_safety: Annotated[float | None, typer.Option(help="臨時覆寫安全邊際")] = None,
) -> None:
    """用透明的機械模型評估合理價區間（非投資建議）。"""
    client = MarketDataClient()
    ticker = symbol.upper()
    adhoc = _adhoc_override(target_pe, required_return, margin_of_safety)
    assumptions_by_symbol, provenance = _resolve_for_symbols(client, [ticker], adhoc)
    assumptions = assumptions_by_symbol[ticker]
    console.print(_describe_assumption_source(ticker, provenance))
    quote = client.quote(ticker)
    fundamentals = client.fundamentals(ticker)
    valuation = build_valuation_report(ticker, quote.last_price, fundamentals, assumptions)
    report.render_valuation(console, valuation, assumptions)


@app.command("dividends")
def show_dividends() -> None:
    """已領股利（含預扣稅）與未來一年配息預估。"""
    overview = service.load_dividend_overview(_settings(), MarketDataClient())
    report.render_dividend_receipts(console, overview.receipts)
    report.render_dividend_forecast(console, overview.forecasts)
    console.print(
        f"[dim]稅後預估採用預扣率 {overview.withholding_rate:.0%}"
        "（台灣居民非美國人股利預扣，可用 PORTFOLIO_NRA_WITHHOLDING 調整）。[/dim]"
    )
    if any(currency_for_symbol(item.symbol) == "TWD" for item in overview.forecasts):
        console.print(
            "[dim]台股不適用美股預扣：單筆股利滿 NT$20,000 才扣 2.11% 二代健保補充保費，"
            "預估以全年股利視為一筆，分次配息者實際可能較少；所得稅依個人申報方式，未計入。[/dim]"
        )


@app.command("snapshot")
def write_snapshot(
    cash_balance: Annotated[
        float | None, typer.Option(help="帳戶現金餘額（券商無 API，需手動給；省略則沿用當日已存的值）")
    ] = None,
) -> None:
    """記錄今天的持倉與帳戶淨值快照，累積出自己的歷史曲線。"""
    settings = _settings()
    performance = _load_performance(MarketDataClient())
    today = date.today()
    with open_store(settings.database_path) as connection:
        cash = _cash_for_snapshot(connection, today, cash_balance)
        save_position_snapshot(connection, performance, today, cash)
    total = performance.total_market_value + cash
    console.print(
        f"[green]已寫入 {today} 快照[/green]："
        f"持倉 ${performance.total_market_value:,.2f} + 現金 ${cash:,.2f} "
        f"= ${total:,.2f}"
    )


def _cash_for_snapshot(connection, snapshot_date: date, given: float | None) -> float:
    """省略 --cash-balance 時沿用當日已存的值，否則每日排程會把手動填的餘額蓋回 0。"""
    if given is not None:
        return given
    return load_cash_balance_on(connection, snapshot_date) or 0.0


@app.command("history")
def show_history() -> None:
    """列出累積至今的帳戶淨值快照。"""
    settings = _settings()
    with open_store(settings.database_path) as connection:
        history = load_account_value_history(connection)
    if not history:
        console.print("[yellow]還沒有任何快照，先跑 `portfolio snapshot`。[/yellow]")
        return
    baseline = history[0][1]
    for snapshot_date, total_value in history:
        change = (total_value / baseline - 1) * 100 if baseline else 0.0
        console.print(f"{snapshot_date}  ${total_value:>14,.2f}   {change:+7.2f}%")


@app.command("add-position")
def add_position(
    symbol: str,
    shares: Annotated[float, typer.Option(help="持有股數")],
    avg_cost: Annotated[float, typer.Option(help="你的平均成本（每股）")],
    note: Annotated[str, typer.Option(help="備註")] = "",
) -> None:
    """手動輸入持倉（沒有 CSV，或匯出檔不含早期買進紀錄時用）。台股填台幣均價。"""
    settings = _settings()
    symbol = _resolve_symbol_or_exit(symbol)
    manual = ManualPosition(symbol=symbol, quantity=shares, average_cost=avg_cost, note=note)
    with open_store(settings.database_path) as connection:
        save_manual_position(connection, manual)
    currency = currency_for_symbol(symbol)
    console.print(
        f"[green]已記錄[/green] {symbol}：{shares:,.4f} 股 @ {format_money(avg_cost, currency)}"
        f"（成本 {format_money(shares * avg_cost, currency)}）"
    )


def _resolve_symbol_or_exit(raw_symbol: str) -> str:
    """`2330` -> `2330.TW` / `2330.TWO`; an unknown code stops the command."""
    try:
        return service.resolve_symbol(MarketDataClient(), raw_symbol)
    except InvalidSymbolError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error


@app.command("remove-position")
def remove_position(symbol: str) -> None:
    """刪除手動輸入的持倉。"""
    settings = _settings()
    with open_store(settings.database_path) as connection:
        removed = delete_manual_position(connection, symbol)
    message = "[green]已刪除[/green]" if removed else "[yellow]找不到該手動持倉[/yellow]"
    console.print(f"{message} {symbol.upper()}")


@app.command("targets")
def show_targets(
    symbols: Annotated[list[str] | None, typer.Argument(help="代號，省略則用持倉+監控清單")] = None,
    target_pe: Annotated[float | None, typer.Option(help="臨時覆寫 target P/E")] = None,
    required_return: Annotated[float | None, typer.Option(help="臨時覆寫要求報酬率")] = None,
    margin_of_safety: Annotated[float | None, typer.Option(help="臨時覆寫安全邊際")] = None,
) -> None:
    """合理價區間、建議入手價、與你的平均成本對照（每檔用自己的假設）。"""
    client = MarketDataClient()
    positions, _ = _load_positions()
    tickers = [s.upper() for s in symbols] if symbols else _default_target_symbols(positions)
    if not tickers:
        console.print("[yellow]沒有可分析的標的，先匯入持倉或加入監控清單。[/yellow]")
        raise typer.Exit(code=1)
    adhoc = _adhoc_override(target_pe, required_return, margin_of_safety)
    assumptions_by_symbol, provenance = _resolve_for_symbols(client, tickers, adhoc)
    targets = build_entry_targets(client, assumptions_by_symbol, positions)
    report.render_entry_targets(console, targets, provenance)


def _default_target_symbols(positions: list[Position]) -> list[str]:
    return service.default_target_symbols(_settings(), positions)


watch_app = typer.Typer(help="價格監控清單與提醒目標", no_args_is_help=True)
app.add_typer(watch_app, name="watch")


@watch_app.command("add")
def watch_add(
    symbol: str,
    buy_below: Annotated[float | None, typer.Option(help="跌破此價提醒")] = None,
    sell_above: Annotated[float | None, typer.Option(help="突破此價提醒")] = None,
) -> None:
    """加入或更新一檔的提醒價位。"""
    if buy_below is None and sell_above is None:
        console.print("[red]至少要給 --buy-below 或 --sell-above 其中一個。[/red]")
        raise typer.Exit(code=1)
    settings = _settings()
    symbol = _resolve_symbol_or_exit(symbol)
    with open_store(settings.database_path) as connection:
        save_alert_rule(connection, symbol, buy_below, sell_above)
    console.print(f"[green]已設定[/green] {symbol}：{_describe_targets(symbol, buy_below, sell_above)}")


def _describe_targets(symbol: str, buy_below: float | None, sell_above: float | None) -> str:
    currency = currency_for_symbol(symbol)
    parts = []
    if buy_below is not None:
        parts.append(f"跌破 {format_money(buy_below, currency)} 提醒")
    if sell_above is not None:
        parts.append(f"突破 {format_money(sell_above, currency)} 提醒")
    return "、".join(parts)


@watch_app.command("remove")
def watch_remove(symbol: str) -> None:
    """從監控清單移除一檔。"""
    settings = _settings()
    with open_store(settings.database_path) as connection:
        removed = delete_alert_rule(connection, symbol)
    message = "[green]已移除[/green]" if removed else "[yellow]不在監控清單中[/yellow]"
    console.print(f"{message} {symbol.upper()}")


@watch_app.command("list")
def watch_list() -> None:
    """顯示監控清單與目前距離。"""
    report.render_watchlist(console, service.load_watchlist(_settings(), MarketDataClient()))


@watch_app.command("auto")
def watch_auto(
    target_pe: Annotated[float | None, typer.Option(help="臨時覆寫 target P/E")] = None,
    required_return: Annotated[float | None, typer.Option(help="臨時覆寫要求報酬率")] = None,
    margin_of_safety: Annotated[float | None, typer.Option(help="臨時覆寫安全邊際")] = None,
) -> None:
    """用每檔自己的估值假設，自動設定所有持倉的入手提醒價。"""
    client = MarketDataClient()
    positions, _ = _load_positions()
    if not positions:
        console.print("[yellow]沒有持倉可以推導目標價。[/yellow]")
        raise typer.Exit(code=1)
    adhoc = _adhoc_override(target_pe, required_return, margin_of_safety)
    assumptions_by_symbol, _ = _resolve_for_symbols(client, [p.symbol for p in positions], adhoc)
    _persist_auto_targets(build_entry_targets(client, assumptions_by_symbol, positions))


def _persist_auto_targets(targets: list) -> None:
    settings = _settings()
    skipped = []
    with open_store(settings.database_path) as connection:
        for target in targets:
            if target.buy_below is None:
                skipped.append(target.symbol)
                continue
            save_alert_rule(connection, target.symbol, target.buy_below, None)
            basis = (
                f"{target.model_count} 個估值模型"
                if target.model_count
                else f"依據 {target.basis}"
            )
            currency = currency_for_symbol(target.symbol)
            console.print(
                f"[green]{target.symbol}[/green] 入手價 {format_money(target.buy_below, currency)}"
                f"（{basis}，現價 {format_money(target.last_price, currency)}）"
            )
    if skipped:
        console.print(f"[yellow]無適用模型、已略過：{'、'.join(skipped)}[/yellow]")


@app.command("check")
def check_alerts(
    notify: Annotated[bool, typer.Option(help="觸發時發送 macOS 桌面通知")] = True,
    resend: Annotated[bool, typer.Option(help="同一天重複觸發也再通知一次")] = False,
) -> None:
    """比對現價與提醒價位，觸發時發送桌面通知（給 launchd 定時跑）。"""
    outcome = service.run_alert_check(_settings(), MarketDataClient(), notify=notify, resend=resend)
    report.render_triggered_alerts(console, outcome.triggered, outcome.notified or not notify)
    for channel in outcome.failed_channels:
        console.print(f"[yellow]通知管道 {channel} 發送失敗。[/yellow]")


signals_app = typer.Typer(
    help="智慧提醒：技術指標 + 均價規則觸發（規則觸發的現況描述，非投資建議）",
    no_args_is_help=True,
)
app.add_typer(signals_app, name="signals")


@signals_app.command("check")
def signals_check(
    symbols: Annotated[list[str] | None, typer.Argument(help="代號，省略則用持倉+監控清單")] = None,
    notify: Annotated[bool, typer.Option(help="新觸發時發送通知（桌面 / Telegram / email）")] = True,
    resend: Annotated[bool, typer.Option(help="今天已通知過也再送一次")] = False,
) -> None:
    """比對技術指標與你的均價，列出觸發的標的並發送一句話摘要（規則觸發，非建議）。"""
    tickers = [s.upper() for s in symbols] if symbols else None
    outcome = service.run_signal_check(
        _settings(),
        MarketDataClient(),
        service.SignalScope(symbols=tickers),
        service.DispatchOptions(notify=notify, resend=resend),
    )
    report.render_signal_check(console, outcome)


@signals_app.command("backtest")
def signals_backtest(
    symbol: str,
    years: Annotated[int, typer.Option(help="回測年數")] = 3,
) -> None:
    """用歷史資料回放規則，看觸發頻率與後續報酬是否合理（上線前先跑這個）。"""
    client = MarketDataClient()
    ticker = symbol.upper()
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=365 * years + 5)
    settings = _settings()
    with open_store(settings.database_path) as connection:
        thresholds = resolve_thresholds(connection, ticker).thresholds
    closes = client.close_prices(ticker, start, end)
    result = backtest_signals(PriceHistory(ticker, closes), thresholds=thresholds)
    report.render_backtest(console, result)


# typer 要求一個函式參數對應一個 CLI 旗標，無法用值物件收攏（見 docs/decisions.md D17）。
@signals_app.command("set")
def signals_set(
    symbol: str,
    average_cost_discount: Annotated[float | None, typer.Option(help="低於均價幾 %% 觸發（正數，如 12）")] = None,
    trend_bias: Annotated[float | None, typer.Option(help="偏離 60 日均線幾 %% 觸發（正數，如 10）")] = None,
    rsi_oversold: Annotated[float | None, typer.Option(help="RSI 低於多少算超賣")] = None,
    rsi_overbought: Annotated[float | None, typer.Option(help="RSI 高於多少算超買")] = None,
) -> None:
    """為某一檔記住智慧提醒門檻（只給要改的，其餘保留）。用 `*` 改通用預設。"""
    _save_threshold(
        ThresholdOverride(
            symbol=symbol,
            average_cost_discount_percent=average_cost_discount,
            short_trend_bias_percent=trend_bias,
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
        )
    )


@signals_app.command("reset")
def signals_reset(symbol: str) -> None:
    """清除某一檔的門檻覆寫，回到通用預設。"""
    settings = _settings()
    with open_store(settings.database_path) as connection:
        removed = delete_threshold_override(connection, symbol)
    message = "[green]已清除[/green]" if removed else "[yellow]該檔沒有覆寫[/yellow]"
    console.print(f"{message} {symbol.upper()}")


@signals_app.command("list")
def signals_list() -> None:
    """列出通用預設門檻與各檔覆寫。"""
    settings = _settings()
    positions, _ = _load_positions()
    with open_store(settings.database_path) as connection:
        default = load_global_thresholds(connection)
        overrides = {o.symbol for o in load_all_threshold_overrides(connection)}
        symbols = sorted({p.symbol for p in positions} | overrides)
        resolved = {s: resolve_thresholds(connection, s) for s in symbols}
    report.render_signal_thresholds(console, default, resolved)
    report.render_signal_subscriptions(console, service.load_signal_subscriptions(settings))


@signals_app.command("on")
def signals_on(symbol: str) -> None:
    """打開某一檔的智慧提醒推播；沒持有也沒在監控清單的代號會額外加入。"""
    _set_signal_subscription(symbol, enabled=True)


@signals_app.command("off")
def signals_off(symbol: str) -> None:
    """關掉某一檔的智慧提醒推播（網頁與 `signals check` 仍會列出）。"""
    _set_signal_subscription(symbol, enabled=False)


def _set_signal_subscription(symbol: str, enabled: bool) -> None:
    try:
        result = service.set_signal_subscription(_settings(), symbol, enabled, MarketDataClient())
    except InvalidSymbolError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error
    state = "[green]會推播[/green]" if result.is_enabled else "[yellow]不推播[/yellow]"
    origin = "、".join(result.sources) or "已不在清單"
    console.print(f"{result.symbol}：智慧提醒{state}（來源：{origin}）")


@signals_app.command("history")
def signals_history(
    symbol: Annotated[str | None, typer.Argument(help="只看某一檔，省略則全部")] = None,
    limit: Annotated[int, typer.Option(help="最多顯示幾筆")] = 30,
) -> None:
    """列出過去實際觸發並通知過的智慧提醒。"""
    events = service.load_signal_history(_settings(), symbol.upper() if symbol else None, limit)
    report.render_signal_history(console, events)


def _save_threshold(override: ThresholdOverride) -> None:
    if override.is_empty():
        console.print("[red]至少要指定一個門檻欄位。[/red]")
        raise typer.Exit(code=1)
    settings = _settings()
    with open_store(settings.database_path) as connection:
        save_threshold_override(connection, override)
    target = "通用預設" if override.symbol == SIGNAL_GLOBAL_KEY else override.symbol.upper()
    console.print(f"[green]已記住 {target} 的智慧提醒門檻。[/green]")


def _describe_assumption_source(symbol: str, provenance: dict) -> str:
    source, historical = provenance.get(symbol, ("default", None))
    labels = {
        "historical": "採用該檔自己的歷史 P/E 中位數",
        "manual": "採用你為此檔儲存的假設",
        "default": "採用通用預設（無可用的歷史 P/E）",
        "命令列": "採用本次命令列指定的假設",
    }
    detail = f"，歷史 P/E 中位數 {historical:.1f}" if historical else ""
    return f"[dim]{symbol} 假設來源：{labels.get(source, source)}{detail}[/dim]"


assume_app = typer.Typer(help="每檔的估值假設（會被記住）", no_args_is_help=True)
app.add_typer(assume_app, name="assume")


@assume_app.command("set")
def assume_set(
    symbol: str,
    target_pe: Annotated[float | None, typer.Option(help="Target P/E")] = None,
    required_return: Annotated[float | None, typer.Option(help="DDM 要求報酬率")] = None,
    margin_of_safety: Annotated[float | None, typer.Option(help="安全邊際")] = None,
) -> None:
    """為某一檔記住估值假設（只給要改的欄位，其餘保留）。"""
    override = AssumptionOverride(
        symbol=symbol,
        target_pe=target_pe,
        required_return=required_return,
        margin_of_safety=margin_of_safety,
    )
    if override.is_empty():
        console.print("[red]至少要指定一個假設欄位。[/red]")
        raise typer.Exit(code=1)
    settings = _settings()
    with open_store(settings.database_path) as connection:
        save_override(connection, override)
    console.print(f"[green]已記住 {symbol.upper()} 的假設。[/green]")


@assume_app.command("default")
def assume_default(
    target_pe: Annotated[float | None, typer.Option(help="Target P/E")] = None,
    required_return: Annotated[float | None, typer.Option(help="DDM 要求報酬率")] = None,
    margin_of_safety: Annotated[float | None, typer.Option(help="安全邊際")] = None,
) -> None:
    """設定所有標的的通用預設假設。"""
    override = AssumptionOverride(
        symbol=GLOBAL_DEFAULT_KEY,
        target_pe=target_pe,
        required_return=required_return,
        margin_of_safety=margin_of_safety,
    )
    if override.is_empty():
        console.print("[red]至少要指定一個假設欄位。[/red]")
        raise typer.Exit(code=1)
    settings = _settings()
    with open_store(settings.database_path) as connection:
        save_override(connection, override)
    console.print("[green]已更新通用預設假設。[/green]")


@assume_app.command("reset")
def assume_reset(symbol: str) -> None:
    """清除某一檔的假設覆寫，回到歷史 P/E 或通用預設。"""
    settings = _settings()
    with open_store(settings.database_path) as connection:
        removed = delete_override(connection, symbol)
    message = "[green]已清除[/green]" if removed else "[yellow]該檔沒有覆寫[/yellow]"
    console.print(f"{message} {symbol.upper()}")


@assume_app.command("list")
def assume_list() -> None:
    """列出通用預設、各檔覆寫，以及每檔實際會用到的假設來源。"""
    settings = _settings()
    client = MarketDataClient()
    positions, _ = _load_positions()
    with open_store(settings.database_path) as connection:
        default = load_global_default(connection)
        overrides = {o.symbol for o in load_all_overrides(connection)}
    symbols = sorted({p.symbol for p in positions} | overrides)
    resolved, provenance = _resolve_for_symbols(
        client, symbols, AssumptionOverride(GLOBAL_DEFAULT_KEY)
    )
    view = report.AssumptionView(symbols=symbols, resolved=resolved, provenance=provenance)
    report.render_assumptions(console, default, view)


@app.command("fx-sync")
def fx_sync() -> None:
    """把每筆交易補上它成交當天的匯率（算台幣報酬率用）。"""
    outcome = backfill_transaction_fx(_settings(), MarketDataClient())
    if outcome.filled == 0 and outcome.is_complete:
        console.print("[green]所有交易都已有匯率，無須補算。[/green]")
        return
    console.print(
        f"[green]{outcome.pair}：已補 {outcome.filled} 筆[/green]"
        f"（快取 {outcome.rates_cached} 個匯率日）"
    )
    if not outcome.is_complete:
        console.print(f"[yellow]仍有 {outcome.still_missing} 筆取不到匯率。[/yellow]")


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option(help="綁定位址；非本機位址必須先設 API token")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="連接埠")] = 8765,
    reload: Annotated[bool, typer.Option(help="開發時自動重載")] = False,
) -> None:
    """啟動網頁介面與 API（手機加到主畫面也連這裡）。"""
    import uvicorn

    settings = _settings()
    try:
        require_secure_binding(settings, host)
    except InsecureExposureError as error:
        console.print(f"[red]拒絕啟動：[/red]{error}")
        raise typer.Exit(code=1) from error
    _print_serve_banner(settings, host, port)
    uvicorn.run("portfolio.web.api:create_app", host=host, port=port, reload=reload, factory=True)


def _print_serve_banner(settings: Settings, host: str, port: int) -> None:
    console.print(f"[green]網頁介面：[/green] http://{host}:{port}")
    if settings.requires_authentication:
        console.print("[green]已啟用 API token 認證。[/green]")
    else:
        console.print("[dim]未設 token，只綁本機；資料不會離開這台電腦。[/dim]")
    console.print("[dim]Ctrl+C 結束。[/dim]")


@app.command("token")
def manage_token(
    write_env: Annotated[bool, typer.Option(help="把新 token 寫進 .env")] = False,
) -> None:
    """產生 API token（手機與遠端連線都需要）。"""
    settings = _settings()
    if settings.requires_authentication and not write_env:
        console.print("[green]目前已設定 token[/green]（值取自 PORTFOLIO_API_TOKEN，不重複顯示）。")
        console.print("[dim]要換新的請加 --write-env。[/dim]")
        return
    token = generate_api_token()
    console.print(f"[bold]PORTFOLIO_API_TOKEN={token}[/bold]")
    if write_env:
        _append_env_line(f"PORTFOLIO_API_TOKEN={token}")
    console.print("[yellow]這是唯一一次顯示，請存進密碼管理器。[/yellow]")


def _append_env_line(line: str) -> None:
    env_path = Path(".env")
    existing = env_path.read_text() if env_path.exists() else ""
    key = line.split("=", 1)[0]
    kept = [row for row in existing.splitlines() if not row.startswith(f"{key}=")]
    env_path.write_text("\n".join([*kept, line]) + "\n")
    console.print(f"[green]已寫入[/green] {env_path.resolve()}")


@app.command("notify-test")
def notify_test() -> None:
    """測試所有已設定的通知管道（桌面 / Telegram）。"""
    settings = _settings()
    channels = build_channels(settings)
    if not channels:
        console.print("[yellow]沒有啟用任何通知管道。[/yellow]")
        raise typer.Exit(code=1)
    for channel in channels:
        result = channel.send("portfolio 測試", "通知管道可用。")
        mark = "[green]✓[/green]" if result.delivered else "[red]✗[/red]"
        console.print(f"{mark} {result.channel}　{result.detail}")


if __name__ == "__main__":
    app()
