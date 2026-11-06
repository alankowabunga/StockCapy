from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, Protocol

from .config import MessageStyle
from .currency import format_money, labeled_money
from .signals import BUY, Signal, SignalCondition

# Every narrated signal ends with this. The tool states facts about the user's own
# rules; the decision is theirs (see product.md principle 1).
DISCLAIMER = "這只是規則觸發，非投資建議。"

# The contract an LLM narrator (the doc's "second layer") is held to. It may only
# rephrase the facts it is given; it must not add a rule, a number, or a call.
LLM_SYSTEM_PROMPT = (
    "你是一個把「已經觸發的股票規則」講成一句話的助手。"
    "嚴格限制：只能重述使用者提供的事實，不得新增任何規則、門檻、數字、預測或買賣建議，"
    "也不得使用「建議」「應該買」「應該賣」「值得進場」這類字眼。"
    "用繁體中文寫成通順的一句話，客觀描述現況即可；不用自己加免責聲明。"
)


@dataclass(frozen=True)
class MessageFormat:
    style: MessageStyle = MessageStyle.FULL
    show_holdings: bool = True


FULL_FORMAT = MessageFormat()


class SignalNarrator(Protocol):
    """Turns a triggered signal into one plain-language sentence.

    Deliberately narrow: a narrator may only describe conditions that already fired.
    It never invents a rule, a threshold, or a buy/sell call. A future LLM-backed
    narrator (the doc's second layer) implements this same contract — it phrases the
    facts more naturally, it does not add judgement of its own.
    """

    def narrate(self, signal: Signal) -> str: ...


class TemplateNarrator:
    """The default, fully deterministic and offline. No model call, so it is testable
    and cannot drift into advice."""

    def narrate(self, signal: Signal) -> str:
        return format_signal_card(signal) + f"\n\n⚠️ 提醒：{DISCLAIMER}"


def format_signal_card(
    signal: Signal,
    narrator: SignalNarrator | None = None,
    message_format: MessageFormat = FULL_FORMAT,
) -> str:
    if not message_format.show_holdings:
        signal = dataclasses.replace(signal, average_cost=None)
    if message_format.style is MessageStyle.COMPACT:
        return _compact_line(signal)
    if message_format.style is MessageStyle.PLAIN:
        return _join_blocks(_plain_blocks(signal))
    return _full_card(signal, narrator)


def _plain_blocks(signal: Signal) -> list[list[str]]:
    """The plain-language lead plus context; drops thresholds and data timestamps."""
    blocks = [_plain_headline_lines(signal), _valuation_block(signal)]
    if signal.notification_note:
        blocks.append([f"🔁 {signal.notification_note}"])
    caveat = _market_caveat(signal)
    if caveat:
        blocks.append([f"🌐 {caveat}"])
    return blocks


def _compact_line(signal: Signal) -> str:
    emoji = "📉" if signal.direction == BUY else "📈"
    price = f"{labeled_money(signal.last_price, signal.currency)}"
    if signal.daily_change_percent is not None:
        price += f"（{signal.daily_change_percent:+.1f}%）"
    gloss = _plain(_headline_condition(signal))
    if signal.severity > 1:
        gloss += f"（另 {signal.severity - 1} 項）"
    line = f"{emoji} {signal.symbol} {price}｜{gloss}"
    if signal.discount_to_average_cost_percent is not None:
        line += f"｜你的持倉 {signal.discount_to_average_cost_percent:+.1f}%"
    return line


def _headline_condition(signal: Signal) -> SignalCondition:
    """The cost condition is already carried by the holding figure, so lead with a market one."""
    market = [c for c in signal.conditions if c is not SignalCondition.BELOW_AVERAGE_COST]
    return market[0] if market else signal.conditions[0]


def _join_blocks(blocks: list[list[str]]) -> str:
    return "\n\n".join("\n".join(block) for block in blocks if block)


def _full_card(signal: Signal, narrator: SignalNarrator | None) -> str:
    """Keep factual metadata and rules even when the optional narrator supplies prose."""
    summary = _summary(signal)
    if narrator is not None and not isinstance(narrator, TemplateNarrator):
        text = narrator.narrate(signal)
        # LLM errors fall back to the full template, not an additional nested card.
        if text != TemplateNarrator().narrate(signal):
            summary = text.replace(f"（{DISCLAIMER}）", "").replace(DISCLAIMER, "").strip()
    # One list of lines per block; blocks are joined with a blank line between them so
    # the card reads as separated sections rather than one dense wall.
    blocks: list[list[str]] = [_plain_headline_lines(signal)]
    valuation = _valuation_block(signal)
    if valuation:
        blocks.append(valuation)
    blocks += [
        [f"🔔 {signal.symbol}｜{signal.severity} 項條件觸發", f"摘要：{summary}"],
        _price_block(signal),
        [
            f"📊 指標資料截至：{signal.snapshot.as_of or '日期未提供'}"
            f"｜調整後收盤價 {format_money(signal.snapshot.last_close, signal.currency)}",
            *(f"• {_clause(signal, condition)}" for condition in signal.conditions),
        ],
    ]
    if signal.notification_note:
        blocks.append([f"🔁 {signal.notification_note}"])
    caveat = _market_caveat(signal)
    if caveat:
        blocks.append([f"🌐 {caveat}"])
    return _join_blocks(blocks)


def _valuation_block(signal: Signal) -> list[str]:
    """The 入手價 view's reference fair value and buy-below price, when available.
    Mechanical model output (see product.md principle 1) — context, not advice."""
    if signal.reference_price is None:
        return []
    line = f"📐 參考合理價 {labeled_money(signal.reference_price, signal.currency)}"
    if signal.buy_below_price:
        gap = (signal.last_price / signal.buy_below_price - 1) * 100
        reached = (
            f"現價比入手價高 {gap:.0f}%，還沒到" if gap > 0
            else f"現價已到或低於入手價 {abs(gap):.0f}%"
        )
        line += f"、入手價 {format_money(signal.buy_below_price, signal.currency)}（{reached}）"
    if signal.valuation_basis:
        line += f"。依據：{signal.valuation_basis}"
    return [line, *_valuation_confidence_lines(signal)]


# Where the Target P/E multiple came from; the default is the least stock-specific.
_MULTIPLE_SOURCE_LABELS = {
    "default": "預設值，非該股自己的歷史水位",
    "historical": "該股自己的歷史 P/E 中位數",
    "manual": "你手動設定",
    "命令列": "命令列臨時指定",
}


def _valuation_confidence_lines(signal: Signal) -> list[str]:
    """How much the number above rests on assumptions. A lone fair value reads as fact;
    stating the model count, the uncertainty and the multiple's origin keeps it honest."""
    context = signal.valuation_context
    if context is None:
        return []
    models = "、".join(context.model_names)
    if context.is_fragile:
        lead = (
            f"⚠️ 這個合理價僅由 {len(context.model_names)} 個模型（{models}）推得，"
            "對假設非常敏感，只能當粗略參考。"
        )
    else:
        lead = f"採計 {len(context.model_names)} 個模型：{models}。"
    uncertainty = f"不確定性：{context.uncertainty_tier}"
    if context.uncertainty_reasons:
        uncertainty += f"（{'、'.join(context.uncertainty_reasons)}）"
    uncertainty += f"，入手價已用 {context.margin_of_safety:.0%} 安全邊際"
    if context.target_pe is not None:
        source = _MULTIPLE_SOURCE_LABELS.get(context.multiple_source, context.multiple_source)
        uncertainty += f"；Target P/E 倍數 {context.target_pe:g}×（{source}）"
    return [lead, uncertainty + "。"]


def _price_block(signal: Signal) -> list[str]:
    if signal.price_source == "quote":
        return [
            f"💵 最新取得報價：{labeled_money(signal.last_price, signal.currency)}",
            f"🕐 擷取時間：{signal.retrieved_at or '未記錄'}｜報價時間：來源未提供",
        ]
    return [f"💵 備援價格：{labeled_money(signal.last_price, signal.currency)}（調整後收盤價，非即時報價）"]


def _summary(signal: Signal) -> str:
    parts = []
    if SignalCondition.BELOW_AVERAGE_COST in signal.conditions:
        parts.append("低於你的持倉成本")
    other = [c for c in signal.conditions if c is not SignalCondition.BELOW_AVERAGE_COST]
    if other:
        # One short explanation avoids repeating every numeric rule in prose.
        parts.append(_plain(other[0]))
    return "；".join(parts) + "。"



class LLMNarrator:
    """The optional "second layer": an LLM rephrases the already-triggered facts into
    a smoother sentence. It is handed only the facts the template would have stated, is
    told it may not add judgement, and its output always gets the disclaimer appended —
    so even a misbehaving model cannot turn an observation into advice. Any error falls
    back to the deterministic template, so a signal is never lost to a flaky API."""

    def __init__(
        self, complete: Callable[[str, str], str], fallback: SignalNarrator | None = None
    ) -> None:
        self._complete = complete
        self._fallback = fallback or TemplateNarrator()

    def narrate(self, signal: Signal) -> str:
        try:
            text = self._complete(LLM_SYSTEM_PROMPT, _facts_for_llm(signal)).strip()
        except Exception:  # noqa: BLE001 - a model/network hiccup must not drop the alert
            return self._fallback.narrate(signal)
        if not text:
            return self._fallback.narrate(signal)
        return _ensure_disclaimer(text)


def _facts_for_llm(signal: Signal) -> str:
    """The neutral fact string the model is allowed to rephrase — nothing more."""
    price = f"{format_money(signal.last_price, signal.currency)}"
    clauses = "、".join(_clause(signal, condition) for condition in signal.conditions)
    facts = f"{signal.symbol} 現價 {price}，觸發的規則：{clauses}。"
    caveat = _market_caveat(signal)
    return facts + caveat if caveat else facts


def _ensure_disclaimer(text: str) -> str:
    if DISCLAIMER in text:
        return text
    return f"{text}（{DISCLAIMER}）"


_DEFAULT_NARRATOR: SignalNarrator = TemplateNarrator()


def narrate_signal(signal: Signal, narrator: SignalNarrator | None = None) -> str:
    return (narrator or _DEFAULT_NARRATOR).narrate(signal)


def anthropic_completion(model: str, api_key: str | None = None) -> Callable[[str, str], str]:
    """Lazily builds an Anthropic-backed completion function. `anthropic` is an optional
    dependency (install the `llm` extra); importing here keeps the base package light and
    the test suite offline."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def complete(system: str, user: str) -> str:
        response = client.messages.create(
            model=model,
            max_tokens=300,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    return complete


def build_narrator(settings) -> SignalNarrator:
    """The deterministic template unless LLM narration is turned on AND a client can be
    built; any failure degrades silently to the template."""
    if not getattr(settings, "llm_narration", False):
        return TemplateNarrator()
    try:
        return LLMNarrator(anthropic_completion(settings.llm_model))
    except Exception:  # noqa: BLE001 - missing package or credentials -> template
        return TemplateNarrator()


def _clause(signal: Signal, condition: SignalCondition) -> str:
    snapshot = signal.snapshot
    thresholds = signal.thresholds
    if condition is SignalCondition.BELOW_AVERAGE_COST:
        if signal.average_cost is None:
            return "低於你的持倉成本（持倉細節已隱藏）"
        discount = signal.discount_to_average_cost_percent or 0.0
        return f"低於你的均價 {abs(discount):.1f}%（均價 {format_money(signal.average_cost, signal.currency)}；設定門檻：低於至少 {thresholds.average_cost_discount_percent:g}%）"
    if condition is SignalCondition.BELOW_SHORT_TREND:
        return f"低於 60 日均線 {abs(snapshot.bias_to_sma_short_percent or 0.0):.1f}%（設定門檻：至少 {thresholds.short_trend_bias_percent:g}%）"
    if condition is SignalCondition.ABOVE_SHORT_TREND:
        return f"高於 60 日均線 {abs(snapshot.bias_to_sma_short_percent or 0.0):.1f}%（設定門檻：至少 {thresholds.short_trend_bias_percent:g}%）"
    if condition is SignalCondition.OVERSOLD_RSI:
        return f"RSI {snapshot.rsi:.1f}，低於設定門檻 {thresholds.rsi_oversold:g}"
    if condition is SignalCondition.OVERBOUGHT_RSI:
        return f"RSI {snapshot.rsi:.1f}，高於設定門檻 {thresholds.rsi_overbought:g}"
    if condition is SignalCondition.BELOW_LOWER_BAND:
        return f"指標收盤價達到或跌破布林下軌（{format_money(snapshot.bands.lower, signal.currency)}）"
    if condition is SignalCondition.ABOVE_UPPER_BAND:
        return f"指標收盤價達到或突破布林上軌（{format_money(snapshot.bands.upper, signal.currency)}）"
    return condition.label


def _plain_headline_lines(signal: Signal) -> list[str]:
    """A plain-language header that leads the card, so a reader who does not know the
    jargon understands it from the first two lines. Describes where the price sits —
    never what to do about it. The detailed, data-transparent block follows below."""
    direction_word = "近期偏低" if signal.direction == BUY else "近期偏高"
    emoji = "📉" if signal.direction == BUY else "📈"
    body = "、".join(_plain_lead_fragment(signal, condition) for condition in signal.conditions)
    headline = f"{emoji} {signal.symbol}：{direction_word}"
    price = f"現在 {labeled_money(signal.last_price, signal.currency)}"
    if signal.daily_change_percent is not None:
        price += f"（今天 {signal.daily_change_percent:+.1f}%）"
    lines = [headline, f"{price}，{body}。"]
    context = _position_context(signal)
    if context:
        lines.append(context)
    holding = _holding_line(signal)
    if holding:
        lines.append(holding)
    return lines


def _position_context(signal: Signal) -> str:
    """Where the price sits in its own year, in plain terms — reinforces 偏高／偏低."""
    parts = []
    percentile = signal.snapshot.range_percentile
    if percentile is not None:
        parts.append(f"位於 52 週區間 {percentile:.0f}%")
    premium = signal.snapshot.bias_to_sma_long_percent
    if premium is not None:
        parts.append(f"{'高' if premium >= 0 else '低'}於 200 日均線 {abs(premium):.0f}%")
    return "；".join(parts) + "。" if parts else ""


def _holding_line(signal: Signal) -> str:
    """Shown only when the symbol is actually held, so you know this is your position."""
    if signal.average_cost is None:
        return ""
    unrealized = signal.discount_to_average_cost_percent or 0.0
    return f"你持倉均價 {labeled_money(signal.average_cost, signal.currency)}，目前未實現 {unrealized:+.1f}%。"


def _plain_lead_fragment(signal: Signal, condition: SignalCondition) -> str:
    """One plain clause for the headline, with the key number baked in where it helps."""
    snapshot = signal.snapshot
    if condition is SignalCondition.ABOVE_UPPER_BAND:
        return f"漲到近一個月的相對高點（碰到高檔參考線約 {format_money(snapshot.bands.upper, signal.currency)}）"
    if condition is SignalCondition.BELOW_LOWER_BAND:
        return f"跌到近一個月的相對低點（碰到低檔參考線約 {format_money(snapshot.bands.lower, signal.currency)}）"
    if condition is SignalCondition.ABOVE_SHORT_TREND:
        return f"比近三個月的平均價高約 {abs(snapshot.bias_to_sma_short_percent or 0.0):.0f}%"
    if condition is SignalCondition.BELOW_SHORT_TREND:
        return f"比近三個月的平均價低約 {abs(snapshot.bias_to_sma_short_percent or 0.0):.0f}%"
    if condition is SignalCondition.OVERBOUGHT_RSI:
        return "近期走勢偏強"
    if condition is SignalCondition.OVERSOLD_RSI:
        return "近期走勢偏弱"
    if condition is SignalCondition.BELOW_AVERAGE_COST and signal.average_cost is None:
        return "比你的買進成本便宜"
    if condition is SignalCondition.BELOW_AVERAGE_COST:
        return f"比你的買進成本便宜約 {abs(signal.discount_to_average_cost_percent or 0.0):.0f}%"
    return _plain(condition)


def _plain(condition: SignalCondition) -> str:
    """A jargon-free gloss of one condition, so a Telegram reader who does not know
    what '布林上軌' means still understands what fired. Describes the fact only — never
    what to do about it."""
    plain_by_condition = {
        SignalCondition.BELOW_AVERAGE_COST: "現在比你的買進成本便宜",
        SignalCondition.BELOW_SHORT_TREND: "低於近 60 個交易日的平均價",
        SignalCondition.ABOVE_SHORT_TREND: "高於近 60 個交易日的平均價",
        SignalCondition.OVERSOLD_RSI: "近期價格走勢偏弱",
        SignalCondition.OVERBOUGHT_RSI: "近期價格走勢偏強",
        SignalCondition.BELOW_LOWER_BAND: "已達到或低於近期波動區間下緣",
        SignalCondition.ABOVE_UPPER_BAND: "已達到或高於近期波動區間上緣",
    }
    return plain_by_condition.get(condition, condition.label)


def _market_caveat(signal: Signal) -> str:
    if not signal.likely_market_wide or signal.market_context is None:
        return ""
    context = signal.market_context
    moved = "下跌" if signal.direction == BUY else "上漲"
    period = f"近 {context.sessions} 個交易日" if context.sessions is not None else "比較期間未提供"
    dates = f"（{context.start_date} 至 {context.end_date}）" if context.start_date and context.end_date else ""
    return f"大盤背景：{context.benchmark_symbol} {period}{dates}也{moved} {abs(context.change_percent):.1f}%（同向變動）。"
