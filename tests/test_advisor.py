import dataclasses

from portfolio.advisor import DISCLAIMER, MessageFormat, TemplateNarrator, format_signal_card, narrate_signal
from portfolio.config import MessageStyle
from portfolio.indicators import BollingerBands, IndicatorSnapshot
from portfolio.signals import MarketContext, PositionPricing, evaluate_signal


BOUGHT_AT_100 = PositionPricing(average_cost=100.0)


def _snapshot(**kwargs) -> IndicatorSnapshot:
    base = dict(
        symbol="NVDA",
        last_close=85.0,
        rsi=25.0,
        sma_short=100.0,
        sma_long=100.0,
        bands=BollingerBands(lower=90.0, middle=105.0, upper=120.0),
    )
    base.update(kwargs)
    return IndicatorSnapshot(**base)


def test_every_narration_ends_with_the_non_advice_disclaimer():
    signal = evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100)
    text = narrate_signal(signal)
    assert text.endswith(f"提醒：{DISCLAIMER}")


def test_narration_states_the_facts_with_numbers():
    signal = evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100)
    text = narrate_signal(signal)
    assert "NVDA｜4 項條件觸發" in text
    assert "備援價格：USD $85.00" in text
    assert "低於你的均價 15.0%" in text
    assert "低於 60 日均線 15.0%" in text
    assert "RSI 25.0，低於設定門檻 30" in text
    assert "跌破布林下軌" in text


def test_market_wide_selloff_adds_a_caveat():
    context = MarketContext(benchmark_symbol="SPY", change_percent=-6.0, sessions=5)
    signal = evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100, market_context=context)
    text = narrate_signal(signal)
    assert "SPY 近 5 個交易日也下跌 6.0%" in text
    assert "同向變動" in text
    assert "比較像" not in text


def test_calm_market_gets_no_caveat():
    context = MarketContext(benchmark_symbol="SPY", change_percent=-1.0)
    signal = evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100, market_context=context)
    assert "大盤整體在動" not in narrate_signal(signal)


def test_sell_side_narration_reads_as_expensive():
    signal = evaluate_signal(
        _snapshot(last_close=140.0, rsi=80.0, bands=BollingerBands(80.0, 100.0, 120.0))
    )
    text = narrate_signal(signal)
    assert "RSI 80.0，高於設定門檻 70" in text
    assert "突破布林上軌" in text
    assert DISCLAIMER in text


def test_narrator_is_the_documented_protocol():
    signal = evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100)
    assert narrate_signal(signal, TemplateNarrator()) == narrate_signal(signal)


# --- Optional LLM narrator (the doc's "second layer") -------------------------

from portfolio.advisor import (  # noqa: E402
    LLMNarrator,
    _ensure_disclaimer,
    build_narrator,
)


def test_llm_narration_appends_the_disclaimer_even_if_the_model_omits_it():
    narrator = LLMNarrator(complete=lambda system, user: "NVDA 目前價格偏低。")
    text = narrator.narrate(evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100))
    assert text.startswith("NVDA 目前價格偏低。")
    assert DISCLAIMER in text


def test_llm_narration_does_not_double_the_disclaimer():
    narrator = LLMNarrator(complete=lambda s, u: f"價格偏低。{DISCLAIMER}")
    text = narrator.narrate(evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100))
    assert text.count(DISCLAIMER) == 1


def test_llm_narration_falls_back_to_template_on_error():
    def boom(system, user):
        raise RuntimeError("api down")

    narrator = LLMNarrator(complete=boom)
    signal = evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100)
    assert narrator.narrate(signal) == narrate_signal(signal)  # template output


def test_llm_narration_falls_back_when_the_model_returns_nothing():
    narrator = LLMNarrator(complete=lambda s, u: "   ")
    signal = evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100)
    assert narrator.narrate(signal) == narrate_signal(signal)


def test_llm_is_only_handed_facts_never_asked_for_a_call():
    captured = {}

    def capture(system, user):
        captured["system"], captured["user"] = system, user
        return "一句話"

    signal = evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100)
    LLMNarrator(complete=capture).narrate(signal)
    assert "不得" in captured["system"]  # the system prompt forbids adding judgement
    assert "$85.00" in captured["user"]  # the user message is just the facts


def test_build_narrator_stays_deterministic_when_llm_is_off(tmp_path):
    from conftest import make_settings

    assert type(build_narrator(make_settings(tmp_path))).__name__ == "TemplateNarrator"


def test_ensure_disclaimer_is_idempotent():
    assert _ensure_disclaimer("hello") == f"hello（{DISCLAIMER}）"
    assert _ensure_disclaimer(f"x {DISCLAIMER}") == f"x {DISCLAIMER}"


def test_narration_adds_a_plain_language_line():
    signal = evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100)
    text = narrate_signal(signal)
    assert "摘要：低於你的持倉成本；低於近 60 個交易日的平均價。" in text
    assert "可能過頭" not in text


def test_plain_language_dejargons_the_upper_band():
    signal = evaluate_signal(
        _snapshot(last_close=140.0, sma_short=140.0, rsi=50.0, bands=BollingerBands(80.0, 100.0, 120.0))
    )
    text = narrate_signal(signal)
    assert "突破布林上軌" in text  # the precise clause is kept
    assert "已達到或高於近期波動區間上緣" in text  # and explained in plain words


def test_live_quote_is_separate_from_dated_adjusted_indicator_prices():
    signal = evaluate_signal(
        _snapshot(as_of="2026-10-09"),
        pricing=PositionPricing(last_price=88, average_cost=100, retrieved_at="2026-10-12 08:00 UTC"),
    )
    text = narrate_signal(signal)
    assert "最新取得報價：USD $88.00" in text
    assert "擷取時間：2026-10-12 08:00 UTC｜報價時間：來源未提供" in text
    assert "指標資料截至：2026-10-09｜調整後收盤價 $85.00" in text


def test_custom_thresholds_are_rendered_instead_of_defaults():
    from portfolio.signals import SignalThresholds
    signal = evaluate_signal(
        _snapshot(rsi=24), pricing=BOUGHT_AT_100,
        thresholds=SignalThresholds(average_cost_discount_percent=14, short_trend_bias_percent=13, rsi_oversold=25),
    )
    text = narrate_signal(signal)
    assert "低於至少 14%" in text
    assert "設定門檻：至少 13%" in text
    assert "RSI 24.0，低於設定門檻 25" in text


def test_card_leads_with_a_plain_headline_for_a_high_signal():
    signal = evaluate_signal(
        _snapshot(last_close=140.0, rsi=80.0, bands=BollingerBands(80.0, 100.0, 120.0))
    )
    text = narrate_signal(signal)
    assert text.startswith("📈 NVDA：近期偏高")
    assert "漲到近一個月的相對高點（碰到高檔參考線約 $120.00）" in text
    # The plain lead comes before the detailed, jargon-heavy block.
    assert text.index("近期偏高") < text.index("項條件觸發")
    # Disclaimer still closes the card.
    assert text.rstrip().endswith(DISCLAIMER)


def test_card_leads_with_a_plain_headline_for_a_low_signal():
    text = narrate_signal(evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100))
    assert text.startswith("📉 NVDA：近期偏低")
    assert "比你的買進成本便宜約 15%" in text


def test_headline_shows_holding_pl_and_daily_change():
    from portfolio.signals import PositionPricing

    signal = evaluate_signal(
        _snapshot(last_close=140.0, rsi=80.0, bands=BollingerBands(80.0, 100.0, 120.0)),
        pricing=PositionPricing(
            last_price=140.0, average_cost=100.0, currency="USD", daily_change_percent=1.5
        ),
    )
    text = narrate_signal(signal)
    assert "（今天 +1.5%）" in text
    assert "你持倉均價 USD $100.00，目前未實現 +40.0%" in text


def test_headline_shows_52_week_position_and_vs_200d():
    import dataclasses

    snap = dataclasses.replace(
        _snapshot(last_close=140.0, rsi=80.0, bands=BollingerBands(80.0, 100.0, 120.0)),
        week52_low=50.0, week52_high=150.0, sma_long=100.0,
    )
    text = narrate_signal(evaluate_signal(snap))
    assert "位於 52 週區間 90%" in text  # (140-50)/(150-50) = 90%
    assert "高於 200 日均線 40%" in text


def test_watch_only_signal_has_no_holding_line():
    text = narrate_signal(
        evaluate_signal(_snapshot(last_close=140.0, rsi=80.0, bands=BollingerBands(80.0, 100.0, 120.0)))
    )
    assert "持倉均價" not in text


def test_valuation_block_shows_reference_and_buy_below_when_available():
    import dataclasses

    base = evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100)  # NVDA buy-side
    signal = dataclasses.replace(
        base, reference_price=120.0, buy_below_price=102.0, valuation_basis="估值模型"
    )
    text = narrate_signal(signal)
    assert "📐 參考合理價 USD $120.00" in text
    assert "入手價 $102.00" in text
    assert "依據：估值模型" in text


def test_valuation_block_absent_when_no_reference_price():
    text = narrate_signal(evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100))
    assert "參考合理價" not in text


def _held_signal(**changes):
    signal = evaluate_signal(_snapshot(), pricing=BOUGHT_AT_100)
    return dataclasses.replace(signal, daily_change_percent=-2.0, **changes)


def _styled(style, show_holdings=True, **changes):
    return format_signal_card(_held_signal(**changes), None, MessageFormat(style, show_holdings))


def test_compact_style_is_one_line_led_by_a_market_condition():
    text = _styled(MessageStyle.COMPACT)
    assert "\n" not in text
    assert text.startswith("📉 NVDA USD $85.00（-2.0%）｜")
    assert "（另 3 項）" in text
    assert text.endswith("｜你的持倉 -15.0%")


def test_plain_style_drops_thresholds_and_data_timestamps():
    text = _styled(MessageStyle.PLAIN, notification_note="條件持續觸發中")
    assert text.startswith("📉 NVDA：近期偏低")
    assert "🔁 條件持續觸發中" in text
    assert "設定門檻" not in text
    assert "指標資料截至" not in text


def test_full_style_is_the_existing_card():
    expected = narrate_signal(_held_signal()).removesuffix(f"\n\n⚠️ 提醒：{DISCLAIMER}")
    assert _styled(MessageStyle.FULL) == expected


def test_hidden_holdings_remove_cost_figures_from_every_style():
    for style in MessageStyle:
        text = _styled(style, show_holdings=False)
        assert "$100.00" not in text
        assert "-15.0%" not in text
        assert "你持倉均價" not in text
