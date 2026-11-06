from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .currency import currency_symbol, format_money
from .markets import currency_for_symbol
from .entry_verdict import (
    TIER_ABOVE_VALUE,
    TIER_AT_MARGIN,
    TIER_BELOW_VALUE,
    TIER_IMPLAUSIBLE,
)
from .dividends import DividendReceipt, ForwardDividendForecast
from .performance import PortfolioPerformance, RiskMetrics
from .valuation import ValuationAssumptions, ValuationReport

@dataclass(frozen=True)
class AssumptionView:
    """Groups the three parallel maps the assumption table needs."""

    symbols: list = field(default_factory=list)
    resolved: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)


MODEL_CAVEAT = (
    "以下估值為機械模型輸出，完全取決於你設定的假設，不是投資建議，也不是「正確價格」。"
    "模型彼此不同意時，代表這檔股票的價值對假設很敏感 —— 那本身就是資訊。"
)


UNKNOWN_MARK = "[yellow]—[/yellow]"


def _colored_amount(amount: float | None) -> str:
    if amount is None:
        return UNKNOWN_MARK
    color = "green" if amount >= 0 else "red"
    return f"[{color}]{amount:+,.2f}[/{color}]"


def _colored_percent(percent: float | None) -> str:
    if percent is None:
        return UNKNOWN_MARK
    color = "green" if percent >= 0 else "red"
    return f"[{color}]{percent:+.2f}%[/{color}]"


def render_holdings(console: Console, performance: PortfolioPerformance) -> None:
    table = Table(title="持倉明細", header_style="bold cyan")
    for column in ("代號", "股數", "平均成本", "現價", "市值", "未實現損益", "報酬率", "權重"):
        table.add_column(column, justify="right" if column != "代號" else "left")
    for position in sorted(performance.positions, key=lambda p: -p.market_value_usd):
        table.add_row(
            position.symbol,
            _quantity_cell(position),
            _average_cost_cell(position),
            f"{position.last_price:,.2f}",
            f"{position.market_value:,.2f}",
            _colored_amount(position.unrealized_gain),
            _colored_percent(position.unrealized_return_percent),
            f"{performance.weight_of(position):.1f}%",
        )
    console.print(table)
    if performance.positions_missing_cost_basis:
        console.print(
            "[dim]* 股數已由股利紀錄證實，但部分股數的買入價不在匯入範圍內，"
            "故平均成本只涵蓋已知部分、報酬率不計算。[/dim]"
        )


def _average_cost_cell(position) -> str:
    if position.has_complete_cost_basis:
        return f"{position.average_cost:,.2f}"
    return f"[dim]{position.average_cost:,.2f}*[/dim]"


def _quantity_cell(position) -> str:
    """Shows every share held, or the market value would not match the count beside it.
    The asterisk marks that part of it has no known purchase price."""
    shown = f"{position.total_quantity:,.4f}".rstrip("0").rstrip(".")
    return shown if position.has_complete_cost_basis else f"{shown}[yellow]*[/yellow]"


def render_portfolio_summary(console: Console, performance: PortfolioPerformance) -> None:
    lines = [
        f"市值合計      ${performance.total_market_value:,.2f}",
        f"投入成本      ${performance.total_cost_basis:,.2f}",
        f"未實現損益    {_colored_amount(performance.total_unrealized_gain)}"
        f"  ({_colored_percent(performance.total_return_percent)})",
        f"已實現損益    {_colored_amount(performance.realized_gain)}",
    ]
    if performance.has_taiwan_positions:
        lines = _two_market_summary_lines(performance, lines)
    lines.extend(_missing_cost_basis_lines(performance))
    console.print(Panel("\n".join(lines), title="帳戶總覽", border_style="cyan"))


def _two_market_summary_lines(performance: PortfolioPerformance, usd_lines: list[str]) -> list[str]:
    """With Taiwan holdings the headline is TWD; each market's own subtotal follows, and
    the USD lines (the snapshot unit) are kept below, labelled."""
    us_value, us_cost = performance.market_subtotal(taiwan=False)
    tw_value, tw_cost = performance.market_subtotal(taiwan=True)
    rate = performance.usd_to_twd
    return [
        f"市值合計      {format_money(performance.total_market_value_twd, 'TWD')}"
        f"（以 USD/TWD {rate:,.4f} 折算）",
        f"投入成本      {format_money(performance.total_cost_basis_twd, 'TWD')}",
        f"  美股小計    {format_money(us_value, 'USD')}（成本 {format_money(us_cost, 'USD')}）",
        f"  台股小計    {format_money(tw_value, 'TWD')}（成本 {format_money(tw_cost, 'TWD')}）",
        "",
        "[dim]以下為美元計價（每日快照與歷史曲線用的單位）[/dim]",
        *usd_lines,
    ]


def _missing_cost_basis_lines(performance: PortfolioPerformance) -> list[str]:
    """Market value and weights are right; only the return is withheld. Say which
    symbol and how to fix it, or the dash looks like a bug."""
    incomplete = performance.positions_missing_cost_basis
    if not incomplete:
        return []
    names = "、".join(
        f"{position.symbol} {position.unpriced_quantity:,.5f} 股" for position in incomplete
    )
    return [
        "",
        f"[yellow]報酬率無法計算[/yellow]：{names} 的買入價不在匯入範圍內。",
        "[dim]市值與權重已包含這些股數。補上均價："
        "sc add-position <代號> --shares <總股數> --avg-cost <均價>[/dim]",
    ]


def render_sector_exposure(console: Console, performance: PortfolioPerformance) -> None:
    table = Table(title="產業曝險", header_style="bold cyan")
    table.add_column("產業")
    table.add_column("權重", justify="right")
    table.add_column("", width=24)
    for sector, weight in performance.exposure_by_sector().items():
        bar = "█" * max(1, round(weight / 100 * 24))
        table.add_row(sector, f"{weight:.1f}%", f"[cyan]{bar}[/cyan]")
    console.print(table)


def render_risk_metrics(console: Console, metrics: RiskMetrics | None) -> None:
    if metrics is None:
        console.print("[yellow]風險指標無法計算：取不到足夠的歷史價格。[/yellow]")
        return
    lines = [
        f"年化波動度      {metrics.annualized_volatility_percent:.1f}%",
        f"最大回撤        [red]{metrics.max_drawdown_percent:.1f}%[/red]",
        f"Sharpe Ratio    {metrics.sharpe_ratio:.2f}",
        f"與 SPY 相關性   {metrics.benchmark_correlation:.2f}",
        f"觀察交易日      {metrics.observation_days} 天",
    ]
    console.print(
        Panel(
            "\n".join(lines),
            title="風險指標（以「目前持倉」回溯計算，非歷史實際交易績效）",
            border_style="yellow",
        )
    )


def render_valuation(
    console: Console, report: ValuationReport, assumptions: ValuationAssumptions
) -> None:
    currency = currency_for_symbol(report.symbol)
    table = Table(title=f"{report.symbol} 估值模型（現價 {format_money(report.last_price, currency)}）",
                  header_style="bold cyan")
    table.add_column("模型")
    table.add_column("估值", justify="right")
    table.add_column("採計", justify="center")
    table.add_column("說明", max_width=46)
    for estimate in report.estimates:
        table.add_row(
            estimate.model_name,
            format_money(estimate.fair_value, currency) if estimate.has_value else "n/a",
            _applicability_label(estimate),
            estimate.note,
        )
    console.print(table)
    console.print(_valuation_verdict(report, assumptions, currency))


def _applicability_label(estimate) -> str:
    if estimate.is_informational:
        return "[dim]對照[/dim]"
    if not estimate.has_value:
        return "[dim]無資料[/dim]"
    return "[green]✓[/green]" if estimate.is_applicable else "[dim]不適用[/dim]"


def _valuation_verdict(
    report: ValuationReport, assumptions: ValuationAssumptions, currency: str = "USD"
) -> Panel:
    midpoint = report.median_fair_value
    if midpoint is None:
        return Panel(
            "沒有任何適用的估值模型 —— 這檔股票無法用本工具的簡單模型評估。",
            border_style="red",
        )
    model_count = len(report.aggregated_values)
    buy_below = report.buy_below_price(assumptions)
    margin = report.effective_margin_of_safety(assumptions)
    assessment = report.uncertainty()
    lines = [
        f"採計模型中位數    {format_money(midpoint, currency)}（{model_count} 個模型）",
        f"相對現價          {_colored_percent(report.upside_percent() or 0)}",
        f"扣 {margin:.0%} 安全邊際後  {format_money(buy_below, currency)}",
        f"不確定性          {assessment.tier}"
        + (f"（{'、'.join(assessment.reasons)}）" if assessment.reasons else ""),
    ]
    if margin > assumptions.margin_of_safety:
        lines.append(
            f"[dim]你設的安全邊際 {assumptions.margin_of_safety:.0%}，因不確定性自動加寬到 "
            f"{margin:.0%}；自己指定安全邊際就會照你的。[/dim]"
        )
    if model_count == 1:
        lines.append("[yellow]只有單一模型適用，結論等同於你的假設，脆弱度高。[/yellow]")
    lines.append(f"\n[dim]{MODEL_CAVEAT}[/dim]")
    return Panel("\n".join(lines), title="估值區間", border_style="magenta")


def render_dividend_receipts(
    console: Console, receipts: dict[str, DividendReceipt]
) -> None:
    if not receipts:
        console.print("[yellow]匯入的紀錄裡沒有股利資料。[/yellow]")
        return
    table = Table(title="已領股利（依券商紀錄）", header_style="bold cyan")
    for column in ("代號", "股利總額", "預扣稅", "實收淨額", "實際稅率"):
        table.add_column(column, justify="right" if column != "代號" else "left")
    for receipt in receipts.values():
        currency = currency_for_symbol(receipt.symbol)
        table.add_row(
            receipt.symbol,
            format_money(receipt.gross_amount, currency),
            f"[red]-{format_money(receipt.withheld_amount, currency)}[/red]",
            f"[green]{format_money(receipt.net_amount, currency)}[/green]",
            f"{receipt.effective_withholding_percent:.1f}%",
        )
    _add_receipt_total_row(table, receipts)
    console.print(table)


def _by_currency(items, amount_of) -> dict[str, list]:
    """Groups rows by their own currency; USD first so US-only output is untouched."""
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(currency_for_symbol(item.symbol), []).append(item)
    return {code: groups[code] for code in sorted(groups, key=lambda code: code != "USD")}


def _total_label(currency: str, grouped: dict) -> str:
    """A lone currency keeps the plain label; mixed ones must say which they sum."""
    if len(grouped) == 1:
        return "[bold]合計[/bold]"
    return f"[bold]合計（{'台股 TWD' if currency == 'TWD' else '美股 USD'}）[/bold]"


def _add_receipt_total_row(table: Table, receipts: dict[str, DividendReceipt]) -> None:
    grouped = _by_currency(receipts.values(), None)
    table.add_section()
    for currency, rows in grouped.items():
        gross = sum(receipt.gross_amount for receipt in rows)
        withheld = sum(receipt.withheld_amount for receipt in rows)
        table.add_row(
            _total_label(currency, grouped),
            f"[bold]{format_money(gross, currency)}[/bold]",
            f"[bold red]-{format_money(withheld, currency)}[/bold red]",
            f"[bold green]{format_money(gross - withheld, currency)}[/bold green]",
            f"{withheld / gross * 100 if gross else 0:.1f}%",
        )


def render_dividend_forecast(
    console: Console, forecasts: list[ForwardDividendForecast]
) -> None:
    if not forecasts:
        console.print("[yellow]目前持倉沒有配息股。[/yellow]")
        return
    table = Table(title="未來一年股利預估（依當前配息率）", header_style="bold cyan")
    for column in ("代號", "股數", "每股年配息", "年化總額", "稅後預估"):
        table.add_column(column, justify="right" if column != "代號" else "left")
    for forecast in forecasts:
        currency = currency_for_symbol(forecast.symbol)
        table.add_row(
            forecast.symbol,
            f"{forecast.quantity:,.2f}",
            format_money(forecast.annual_rate_per_share, currency),
            format_money(forecast.gross_annual_income, currency),
            f"[green]{format_money(forecast.net_annual_income, currency)}[/green]",
        )
    _add_forecast_total_row(table, forecasts)
    console.print(table)


def _add_forecast_total_row(table: Table, forecasts: list[ForwardDividendForecast]) -> None:
    grouped = _by_currency(forecasts, None)
    table.add_section()
    for currency, rows in grouped.items():
        gross = sum(forecast.gross_annual_income for forecast in rows)
        net = sum(forecast.net_annual_income for forecast in rows)
        table.add_row(
            _total_label(currency, grouped), "", "",
            f"[bold]{format_money(gross, currency)}[/bold]",
            f"[bold green]{format_money(net, currency)}[/bold green]",
        )


def _format_gap(gap_percent: float | None) -> str:
    if gap_percent is None:
        return "[dim]—[/dim]"
    if gap_percent <= 0:
        return f"[bold green]已到價 {gap_percent:+.1f}%[/bold green]"
    color = "yellow" if gap_percent <= 15 else "dim"
    return f"[{color}]還差 {gap_percent:.1f}%[/{color}]"


def _format_optional_price(value: float | None, currency: str = "USD") -> str:
    return format_money(value, currency) if value else "[dim]—[/dim]"


def render_entry_targets(console: Console, targets: list, provenance: dict | None = None) -> None:
    _render_entry_price_table(console, targets)
    _render_entry_verdict_table(console, targets)
    _render_price_position_table(console, targets, provenance or {})
    console.print(f"[dim]{MODEL_CAVEAT}[/dim]")


TIER_COLORS = {
    TIER_ABOVE_VALUE: "dim",
    TIER_BELOW_VALUE: "cyan",
    TIER_AT_MARGIN: "green",
    TIER_IMPLAUSIBLE: "yellow",
}


def _render_entry_verdict_table(console: Console, targets: list) -> None:
    table = Table(title="進場判斷（級距依你設定的安全邊際）", header_style="bold cyan")
    for column in ("代號", "vs 參考價", "級距", "建議", "支撐"):
        table.add_column(column, justify="right" if column != "代號" else "left")
    for target in targets:
        table.add_row(target.symbol, *_verdict_cells(target))
    console.print(table)
    console.print(
        "[dim]只有兩個以上模型一致時才給建議。「支撐」是這個結論靠幾個模型 —— "
        "單一模型的結論等同於單一假設。[/dim]"
    )


def _verdict_cells(target) -> tuple[str, str, str, str]:
    verdict = target.verdict
    if verdict is None:
        return ("[dim]—[/dim]", "[dim]無法估值[/dim]", "[dim]—[/dim]", "[dim]—[/dim]")
    color = TIER_COLORS.get(verdict.tier, "white")
    hint = f"[green]{verdict.action_hint}[/green]" if verdict.has_action_hint else (
        f"[dim]{verdict.action_hint}[/dim]"
    )
    return (
        _format_signed_percent(target.upside_percent),
        f"[{color}]{verdict.tier}[/{color}]",
        hint,
        _basis_label(target),
    )


ASSUMPTION_SOURCE_LABELS = {
    "historical": "[green]歷史P/E[/green]",
    "manual": "[cyan]手動[/cyan]",
    "default": "[yellow]預設[/yellow]",
    "命令列": "[magenta]命令列[/magenta]",
}


def _render_entry_price_table(console: Console, targets: list) -> None:
    table = Table(title="入手價對照", header_style="bold cyan")
    for column in ("代號", "現價", "我的均價", "vs 均價", "參考價", "入手價", "距離", "依據"):
        table.add_column(column, justify="right" if column != "代號" else "left")
    for target in targets:
        currency = currency_for_symbol(target.symbol)
        table.add_row(
            target.symbol,
            f"{target.last_price:,.2f}",
            _format_optional_price(target.average_cost, currency),
            _format_signed_percent(target.discount_to_average_cost_percent),
            _format_optional_price(target.reference_price, currency),
            _format_optional_price(target.buy_below, currency),
            _format_gap(target.gap_to_buy_below_percent),
            _basis_label(target),
        )
    console.print(table)


def _basis_label(target) -> str:
    if target.basis == "估值模型":
        color = "yellow" if target.model_count == 1 else "green"
        return f"[{color}]{target.model_count} 模型[/{color}]"
    if target.basis == "200日均線":
        return "[cyan]200MA[/cyan]"
    return "[dim]無[/dim]"


def _render_price_position_table(console: Console, targets: list, provenance: dict) -> None:
    table = Table(title="價格位置與假設來源", header_style="bold cyan")
    for column in ("代號", "現在P/E", "歷史P/E", "區間位置", "vs 200MA", "假設來源"):
        table.add_column(column, justify="right" if column != "代號" else "left")
    for target in targets:
        position = target.price_position
        source, historical = provenance.get(target.symbol, ("default", None))
        table.add_row(
            target.symbol,
            f"{target.trailing_pe:,.1f}" if target.trailing_pe else "[dim]—[/dim]",
            f"{historical:,.1f}" if historical else "[dim]—[/dim]",
            _format_range_percentile(position.range_percentile),
            _format_signed_percent(position.premium_to_two_hundred_day_percent),
            ASSUMPTION_SOURCE_LABELS.get(source, source),
        )
    console.print(table)
    console.print("[dim]區間位置：0% = 52 週最低、100% = 52 週最高。此表與內在價值無關。[/dim]")


def _format_range_percentile(percentile: float | None) -> str:
    if percentile is None:
        return "[dim]—[/dim]"
    filled = max(1, round(percentile / 100 * 10))
    color = "green" if percentile <= 35 else ("yellow" if percentile <= 70 else "red")
    return f"[{color}]{percentile:.0f}%[/{color}] {'▁' * filled}"


def _format_signed_percent(percent: float | None) -> str:
    if percent is None:
        return "[dim]—[/dim]"
    return _colored_percent(percent)


def render_watchlist(console: Console, statuses: list) -> None:
    if not statuses:
        console.print("[yellow]監控清單是空的，用 `portfolio watch add <SYM> --buy-below <價格>`。[/yellow]")
        return
    table = Table(title="價格監控清單", header_style="bold cyan")
    for column in ("代號", "現價", "入手價", "出場價", "距入手價", "狀態"):
        table.add_column(column, justify="right" if column != "代號" else "left")
    for status in statuses:
        table.add_row(
            status.rule.symbol,
            f"{status.last_price:,.2f}",
            _format_optional_price(status.rule.buy_below),
            _format_optional_price(status.rule.sell_above),
            _format_gap(status.distance_to_buy_percent),
            _watchlist_state_label(status),
        )
    console.print(table)


def _watchlist_state_label(status) -> str:
    if status.is_buy_triggered:
        return "[bold green]買點觸發[/bold green]"
    if status.is_sell_triggered:
        return "[bold red]賣點觸發[/bold red]"
    return "[dim]監控中[/dim]"


def render_triggered_alerts(console: Console, alerts: list, notified: bool) -> None:
    if not alerts:
        console.print("[dim]目前沒有觸發任何價格提醒。[/dim]")
        return
    lines = [f"• {alert.headline}" for alert in alerts]
    if not notified:
        lines.append("\n[yellow]桌面通知發送失敗（osascript 不可用）。[/yellow]")
    console.print(Panel("\n".join(lines), title="價格提醒觸發", border_style="green"))


def render_assumptions(console: Console, default, view: "AssumptionView") -> None:
    console.print(
        Panel(
            f"Target P/E {default.target_price_earnings:.1f}　"
            f"要求報酬率 {default.required_return:.1%}　"
            f"股利成長 {default.dividend_growth:.1%}　"
            f"安全邊際 {default.margin_of_safety:.0%}",
            title="通用預設假設",
            border_style="cyan",
        )
    )
    if not view.symbols:
        console.print("[yellow]還沒有任何持倉或覆寫。[/yellow]")
        return
    console.print(_assumption_table(view))
    console.print(
        "[dim]歷史P/E 只在四分位寬度 <= 中位數的 0.8 倍時才採用；"
        "太寬代表 EPS 變動劇烈、trailing P/E 失去意義，會退回預設。[/dim]"
    )


def _assumption_table(view: "AssumptionView") -> Table:
    table = Table(title="各檔實際採用的假設", header_style="bold cyan")
    for column in ("代號", "Target P/E", "要求報酬率", "安全邊際", "來源", "歷史P/E中位數"):
        table.add_column(column, justify="right" if column != "代號" else "left")
    for symbol in view.symbols:
        assumptions = view.resolved[symbol]
        source, historical = view.provenance.get(symbol, ("default", None))
        table.add_row(
            symbol,
            f"{assumptions.target_price_earnings:.1f}",
            f"{assumptions.required_return:.1%}",
            f"{assumptions.margin_of_safety:.0%}",
            ASSUMPTION_SOURCE_LABELS.get(source, source),
            f"{historical:.1f}" if historical else "[dim]—[/dim]",
        )
    return table


CURRENCY_CAVEAT = (
    "匯率為 Yahoo 參考匯率，非你複委託實際成交匯率（券商有買賣價差），"
    "故與對帳單會有小幅差異。"
)


def render_currency_attribution(console: Console, performance, home_currency: str) -> None:
    if not performance.has_home_currency_data:
        console.print(
            "[yellow]沒有匯率資料，無法換算。跑 `portfolio fx-sync` 補上每筆交易的成交日匯率。[/yellow]"
        )
        return
    console.print(_attribution_panel(performance, home_currency))
    console.print(_attribution_table(performance, home_currency))
    console.print(f"[dim]{CURRENCY_CAVEAT}[/dim]")


def _home_amount(symbol: str, value: float) -> str:
    return f"{symbol}{value:,.0f}"


def _colored_whole_amount(value: float) -> str:
    """Home-currency cents are noise at these magnitudes."""
    color = "green" if value >= 0 else "red"
    return f"[{color}]{value:+,.0f}[/{color}]"


def _signed_home_amount(symbol: str, value: float) -> str:
    color = "green" if value >= 0 else "red"
    sign = "+" if value >= 0 else "-"
    return f"[{color}]{sign}{symbol}{abs(value):,.0f}[/{color}]"


def _attribution_panel(performance, home_currency: str) -> Panel:
    mark = currency_symbol(home_currency)
    
    lines = [
        f"市值          {_home_amount(mark, performance.total_market_value_home):>16}",
        f"投入成本      {_home_amount(mark, performance.total_cost_home):>16}",
        f"總損益        {_signed_home_amount(mark, performance.total_gain_home)}"
        f"  ({_colored_percent(performance.total_return_home_percent)})",
        "",
        f"　其中 股票漲跌  {_signed_home_amount(mark, performance.total_stock_gain_home)}",
        f"　其中 匯率變動  {_signed_home_amount(mark, performance.total_currency_gain_home)}",
    ]
    return Panel(
        "\n".join(lines),
        title=f"{home_currency} 損益歸因（股票 vs 匯率）",
        border_style="cyan",
    )


def _attribution_table(performance, home_currency: str) -> Table:
    """The currency lives in the title: repeating NT$ in every cell overflows the table."""
    mark = currency_symbol(home_currency).strip()
    table = Table(title=f"各檔損益拆解（單位：{mark}）", header_style="bold cyan")
    for column in ("代號", "成本均價匯率", "現在匯率", "成本", "股票漲跌", "匯率變動", "合計"):
        table.add_column(column, justify="right" if column != "代號" else "left")
    for position in sorted(performance.positions, key=lambda item: -item.market_value):
        attribution = position.attribution
        if attribution is None:
            continue
        table.add_row(
            position.symbol,
            f"{attribution.average_cost_rate:.3f}",
            f"{attribution.rate_now:.3f}",
            f"{attribution.cost_home:,.0f}",
            _colored_whole_amount(attribution.stock_gain_home),
            _colored_whole_amount(attribution.currency_gain_home),
            _colored_whole_amount(attribution.total_gain_home),
        )
    return table


SIGNAL_CAVEAT = (
    "以下為規則觸發的現況描述，不是買賣建議。門檻是你自己設的；"
    "上線前請先用 `stockcapy signals backtest <SYM>` 看觸發頻率是否合理。"
)

_DIRECTION_LABEL = {"BUY": "[green]偏低[/green]", "SELL": "[red]偏高[/red]"}


def render_signals(console: Console, signals: list) -> None:
    from .advisor import narrate_signal

    if not signals:
        console.print("[dim]目前沒有任何標的觸發智慧提醒規則。[/dim]")
        return
    console.print(f"[dim]{SIGNAL_CAVEAT}[/dim]")
    console.print(_signal_table(signals))
    lines = [f"• {narrate_signal(signal)}" for signal in signals]
    console.print(Panel("\n".join(lines), title="一句話摘要", border_style="cyan"))


def _signal_table(signals: list) -> Table:
    table = Table(title="智慧提醒（規則觸發）", header_style="bold cyan")
    for column in ("代號", "方向", "現價", "觸發", "條件", "可能大盤同向"):
        table.add_column(column, justify="right" if column not in {"代號", "條件"} else "left")
    for signal in signals:
        table.add_row(
            f"{signal.symbol} [yellow](不推播)[/yellow]" if signal.is_muted else signal.symbol,
            _DIRECTION_LABEL.get(signal.direction, signal.direction),
            f"{signal.last_price:,.2f}",
            str(signal.severity),
            "、".join(condition.label for condition in signal.conditions),
            "[yellow]是[/yellow]" if signal.likely_market_wide else "[dim]否[/dim]",
        )
    return table


def render_signal_check(console: Console, outcome) -> None:
    stale = getattr(outcome, "stale", [])
    if outcome.signals or not stale:
        render_signals(console, outcome.signals)
    _render_stale_feed_warning(console, stale)
    if outcome.fresh:
        console.print(f"[green]本次新觸發 {len(outcome.fresh)} 檔[/green]（{_delivery_state(outcome)}）。")
    elif outcome.signals:
        console.print("[dim]觸發的標的今天都已通知過，未重複發送。[/dim]")
    for channel in outcome.failed_channels:
        console.print(f"[yellow]通知管道 {channel} 發送失敗。[/yellow]")


def _render_stale_feed_warning(console: Console, stale: list) -> None:
    """An empty signal list from a broken feed looks exactly like a calm market."""
    if not stale:
        return
    names = "、".join(f"{item.symbol}（{item.describe_age}）" for item in stale)
    # Skipped symbols were never judged, so "nothing triggered" would be a claim the
    # tool cannot make — it is suppressed above when every symbol was skipped.
    console.print(
        f"[yellow]行情資料過期，已略過 {len(stale)} 檔，不發出訊號[/yellow]：{names}。"
    )
    console.print(
        "[dim]指標全部來自收盤序列，即使即時報價正常，用舊收盤算出的觸發也是在描述過去。[/dim]"
    )


def _delivery_state(outcome) -> str:
    if not outcome.notify_requested:
        return "未發送，--no-notify 不改動已發送狀態"
    if outcome.notified:
        return "已送出通知"
    return "沒送出去，下個檢查週期會重試"


def render_backtest(console: Console, result) -> None:
    if not result.evaluable_days:
        console.print(f"[yellow]{result.symbol} 的歷史資料不足以回測。[/yellow]")
        return
    console.print(_backtest_summary_panel(result))
    _render_forward_returns(console, result)
    if result.episodes:
        console.print(_episode_table(result.episodes))


def _backtest_summary_panel(result) -> Panel:
    return Panel(
        f"可評估交易日 {result.evaluable_days} 天，觸發 {result.trigger_days} 天"
        f"（{result.trigger_day_ratio:.1%}），共 {result.episode_count} 段。\n"
        "觸發比例接近 100% 代表門檻太鬆、訊號沒有資訊量；接近 0% 代表門檻太嚴。",
        title=f"{result.symbol} 規則回測",
        border_style="cyan",
    )


def _episode_table(episodes) -> Table:
    table = Table(title="觸發區間", header_style="bold cyan")
    for column in ("方向", "起", "迄", "最多同時條件數"):
        table.add_column(column, justify="right" if column == "最多同時條件數" else "left")
    for episode in episodes:
        table.add_row(
            _DIRECTION_LABEL.get(episode.direction, episode.direction),
            episode.start.isoformat(),
            episode.end.isoformat(),
            str(episode.peak_severity),
        )
    return table


def _render_forward_returns(console: Console, result) -> None:
    stats = [s for s in result.forward_returns if s.sample_size]
    if not stats:
        return
    table = Table(
        title="買點訊號的後續報酬（進場當日算起，還原股價）", header_style="bold cyan"
    )
    for column in ("持有天數", "樣本數", "勝率", "報酬中位數", "報酬平均"):
        table.add_column(column, justify="right" if column != "持有天數" else "left")
    for stat in stats:
        table.add_row(
            f"{stat.horizon_days} 天",
            str(stat.sample_size),
            f"{stat.win_rate_percent:.0f}%",
            _colored_percent(stat.median_return_percent),
            _colored_percent(stat.average_return_percent),
        )
    console.print(table)
    console.print(
        "[dim]勝率是「觸發後上漲」的比例；這是規則的事後表現，不保證未來，也不是投資建議。[/dim]"
    )


def _prettify_conditions(conditions: str) -> str:
    """'BUY:BELOW_AVERAGE_COST,OVERSOLD_RSI' -> '低於你的均價、RSI 進入超賣區'."""
    from .signals import SignalCondition

    _, _, codes = conditions.partition(":")
    labels = []
    for code in filter(None, codes.split(",")):
        try:
            labels.append(SignalCondition[code].label)
        except KeyError:
            labels.append(code)
    return "、".join(labels)


def render_signal_subscriptions(console: Console, subscriptions: list) -> None:
    if not subscriptions:
        console.print("[dim]沒有任何股票會收到智慧提醒。用 `signals on <SYM>` 加入。[/dim]")
        return
    table = Table(title="智慧提醒通知對象", header_style="bold cyan")
    for column in ("代號", "來源", "推播"):
        table.add_column(column)
    for item in subscriptions:
        state = "[green]開[/green]" if item.is_enabled else "[yellow]關[/yellow]"
        table.add_row(item.symbol, "、".join(item.sources), state)
    console.print(table)
    console.print("[dim]`signals off <SYM>` 關掉一檔、`signals on <SYM>` 打開或加入一檔。[/dim]")


def render_signal_thresholds(console: Console, default, resolved: dict) -> None:
    console.print(_default_thresholds_panel(default))
    if not resolved:
        console.print("[dim]沒有任何持倉或每檔覆寫。用 `signals set <SYM> --rsi-oversold 25`。[/dim]")
        return
    console.print(_resolved_thresholds_table(resolved))


def _default_thresholds_panel(default) -> Panel:
    return Panel(
        f"低於均價 {default.average_cost_discount_percent:.0f}%、"
        f"偏離 60 日均線 {default.short_trend_bias_percent:.0f}%、"
        f"RSI < {default.rsi_oversold:.0f} 超賣、RSI > {default.rsi_overbought:.0f} 超買",
        title="智慧提醒門檻・通用預設",
        border_style="cyan",
    )


def _resolved_thresholds_table(resolved: dict) -> Table:
    table = Table(title="各檔實際採用的門檻", header_style="bold cyan")
    for column in ("代號", "低於均價%", "均線乖離%", "RSI超賣", "RSI超買", "來源"):
        table.add_column(column, justify="right" if column != "代號" else "left")
    for symbol, entry in resolved.items():
        thresholds = entry.thresholds
        source = "[yellow]手動[/yellow]" if entry.source == "manual" else "[dim]預設[/dim]"
        table.add_row(
            symbol,
            f"{thresholds.average_cost_discount_percent:.0f}",
            f"{thresholds.short_trend_bias_percent:.0f}",
            f"{thresholds.rsi_oversold:.0f}",
            f"{thresholds.rsi_overbought:.0f}",
            source,
        )
    return table


def render_signal_history(console: Console, events: list) -> None:
    if not events:
        console.print("[dim]還沒有任何智慧提醒觸發紀錄。[/dim]")
        return
    table = Table(title="智慧提醒觸發歷史", header_style="bold cyan")
    for column in ("日期", "代號", "方向", "觸發條件", "當時價"):
        table.add_column(column, justify="right" if column in {"當時價"} else "left")
    for event in events:
        table.add_row(
            event.event_date,
            event.symbol,
            _DIRECTION_LABEL.get(event.direction, event.direction),
            _prettify_conditions(event.conditions),
            f"{event.last_price:,.2f}",
        )
    console.print(table)
