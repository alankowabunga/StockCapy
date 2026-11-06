from __future__ import annotations

from ..advisor import narrate_signal
from ..alerts import AlertStatus, TriggeredAlert
from ..currency import currency_symbol
from ..holdings import ManualPosition
from ..indicators import SHORT_TREND_WINDOW
from ..performance import PortfolioPerformance, PositionPerformance, RiskMetrics
from ..service import ClosePoint, DividendOverview, ImportOutcome
from ..signals import Signal
from ..targets import EntryTarget
from ..valuation import ValuationAssumptions, ValuationEstimate

WATCH_STATE_BUY = "buy_triggered"
WATCH_STATE_SELL = "sell_triggered"
WATCH_STATE_WATCHING = "watching"


def _home_currency_fields(position: PositionPerformance) -> dict:
    attribution = position.attribution
    if attribution is None:
        return {
            "average_cost_rate": None,
            "cost_basis_home": None,
            "stock_gain_home": None,
            "currency_gain_home": None,
            "total_gain_home": None,
        }
    return {
        "average_cost_rate": attribution.average_cost_rate,
        "cost_basis_home": attribution.cost_home,
        "stock_gain_home": attribution.stock_gain_home,
        "currency_gain_home": attribution.currency_gain_home,
        "total_gain_home": attribution.total_gain_home,
    }


def serialize_position(
    position: PositionPerformance, performance: PortfolioPerformance
) -> dict:
    return {
        **_home_currency_fields(position),
        "symbol": position.symbol,
        "quantity": position.total_quantity,
        "priced_quantity": position.quantity,
        "unpriced_quantity": position.unpriced_quantity,
        "has_complete_cost_basis": position.has_complete_cost_basis,
        "average_cost": position.average_cost,
        "last_price": position.last_price,
        "market_value": position.market_value,
        "unrealized_gain": position.unrealized_gain,
        "unrealized_return_percent": position.unrealized_return_percent,
        "weight_percent": performance.weight_of(position),
        "sector": position.sector or "Unknown",
    }


def _home_currency_summary(performance: PortfolioPerformance, home_currency: str) -> dict:
    symbol = currency_symbol(home_currency)
    if not performance.has_home_currency_data:
        return {
            "home_currency": home_currency,
            "home_currency_symbol": symbol,
            "rate_now": None,
            "available": False,
        }
    return {
        "home_currency": home_currency,
        "home_currency_symbol": symbol,
        "rate_now": performance.positions[0].rate_now,
        "available": True,
        "market_value_home": performance.total_market_value_home,
        "cost_basis_home": performance.total_cost_home,
        "total_gain_home": performance.total_gain_home,
        "stock_gain_home": performance.total_stock_gain_home,
        "currency_gain_home": performance.total_currency_gain_home,
        "total_return_home_percent": performance.total_return_home_percent,
        # The split needs a known cost, so positions without one drop out. Saying which
        # keeps the panel from reading as the whole portfolio.
        "excluded_symbols": [p.symbol for p in performance.positions_missing_cost_basis],
        "covers_position_count": len(performance.attributions),
        "position_count": len(performance.positions),
    }


def _market_subtotals(performance: PortfolioPerformance) -> dict | None:
    """Only present with Taiwan holdings: TWD is the headline then, with each market's
    own subtotal beside it. The USD figures above stay the snapshot unit (D31)."""
    if not performance.has_taiwan_positions:
        return None
    us_value, us_cost = performance.market_subtotal(taiwan=False)
    tw_value, tw_cost = performance.market_subtotal(taiwan=True)
    return {
        "usd_to_twd": performance.usd_to_twd,
        "market_value_twd": performance.total_market_value_twd,
        "cost_basis_twd": performance.total_cost_basis_twd,
        "us": {"currency": "USD", "market_value": us_value, "cost_basis": us_cost},
        "tw": {"currency": "TWD", "market_value": tw_value, "cost_basis": tw_cost},
    }


def serialize_portfolio(performance: PortfolioPerformance, home_currency: str = "TWD") -> dict:
    return {
        "summary": {
            "market_value": performance.total_market_value,
            "cost_basis": performance.total_cost_basis,
            "unrealized_gain": performance.total_unrealized_gain,
            "unrealized_return_percent": performance.total_return_percent,
            "realized_gain": performance.realized_gain,
            "has_complete_cost_basis": performance.has_complete_cost_basis,
            "market_subtotals": _market_subtotals(performance),
        },
        "home_currency_summary": _home_currency_summary(performance, home_currency),
        "positions": [serialize_position(p, performance) for p in performance.positions],
        "sector_exposure": [
            {"sector": sector, "weight_percent": weight}
            for sector, weight in performance.exposure_by_sector().items()
        ],
    }


def serialize_estimate(estimate: ValuationEstimate) -> dict:
    return {
        "model_name": estimate.model_name,
        "fair_value": estimate.fair_value,
        "is_applicable": estimate.is_applicable,
        "is_informational": estimate.is_informational,
        "note": estimate.note,
    }


def serialize_target(target: EntryTarget, provenance: tuple[str, float | None]) -> dict:
    position = target.price_position
    return {
        "symbol": target.symbol,
        "last_price": target.last_price,
        "average_cost": target.average_cost,
        "discount_to_average_cost_percent": target.discount_to_average_cost_percent,
        "reference_price": target.reference_price,
        "buy_below": target.buy_below,
        "gap_to_buy_below_percent": target.gap_to_buy_below_percent,
        "basis": target.basis,
        "model_count": target.model_count,
        "trailing_pe": target.trailing_pe,
        "week52_low": position.week52_low or None,
        "week52_high": position.week52_high or None,
        "range_percentile": position.range_percentile,
        "premium_to_two_hundred_day_percent": position.premium_to_two_hundred_day_percent,
        "assumption_source": provenance[0],
        "historical_median_pe": provenance[1],
        "estimates": [serialize_estimate(estimate) for estimate in target.valuation.estimates],
    }


def serialize_risk(metrics: RiskMetrics | None) -> dict | None:
    if metrics is None:
        return None
    return {
        "annualized_volatility_percent": metrics.annualized_volatility_percent,
        "max_drawdown_percent": metrics.max_drawdown_percent,
        "sharpe_ratio": metrics.sharpe_ratio,
        "benchmark_correlation": metrics.benchmark_correlation,
        "observation_days": metrics.observation_days,
    }


def serialize_target_pe_candidate(candidate) -> dict:
    return {
        "label": candidate.label,
        "rationale": candidate.rationale,
        "target_pe": candidate.target_pe,
        "is_current": candidate.is_current,
        "fair_value": candidate.fair_value,
        "buy_below": candidate.buy_below,
        "upside_percent": candidate.upside_percent,
        "model_count": candidate.model_count,
        "premium_to_market_pe_percent": candidate.premium_to_market_pe_percent,
        **_verdict_fields(candidate.verdict),
    }


def _verdict_fields(verdict) -> dict:
    if verdict is None:
        return {"tier": None, "action_hint": None, "is_fragile": True}
    return {
        "tier": verdict.tier,
        "action_hint": verdict.action_hint,
        "is_fragile": verdict.is_fragile,
        "has_action_hint": verdict.has_action_hint,
    }


def serialize_target_pe_choices(choices) -> dict:
    return {
        "symbol": choices.symbol,
        "last_price": choices.last_price,
        "trailing_pe": choices.trailing_pe,
        "historical_note": choices.historical_note,
        "candidates": [serialize_target_pe_candidate(c) for c in choices.candidates],
    }


def serialize_stale_signal_data(item) -> dict:
    return {
        "symbol": item.symbol,
        "as_of": item.as_of,
        "age_days": item.age_days,
        "describe_age": item.describe_age,
    }


def serialize_price_history(symbol: str, points: list[ClosePoint]) -> dict:
    return {
        "symbol": symbol.upper(),
        "moving_average_window": SHORT_TREND_WINDOW,
        "points": [
            {"date": point.price_date, "close": point.close, "moving_average": point.moving_average}
            for point in points
        ],
    }


def serialize_dividend_receipt(receipt) -> dict:
    return {
        "symbol": receipt.symbol,
        "gross_amount": receipt.gross_amount,
        "withheld_amount": receipt.withheld_amount,
        "net_amount": receipt.net_amount,
        "effective_withholding_percent": receipt.effective_withholding_percent,
    }


def serialize_dividend_forecast(forecast) -> dict:
    return {
        "symbol": forecast.symbol,
        "quantity": forecast.quantity,
        "annual_rate_per_share": forecast.annual_rate_per_share,
        "gross_annual_income": forecast.gross_annual_income,
        "net_annual_income": forecast.net_annual_income,
    }


def serialize_dividends(overview: DividendOverview) -> dict:
    return {
        "withholding_rate": overview.withholding_rate,
        "receipts": [serialize_dividend_receipt(r) for r in overview.receipts.values()],
        "forecasts": [serialize_dividend_forecast(f) for f in overview.forecasts],
    }


def _watch_state(status: AlertStatus) -> str:
    if status.is_buy_triggered:
        return WATCH_STATE_BUY
    if status.is_sell_triggered:
        return WATCH_STATE_SELL
    return WATCH_STATE_WATCHING


def serialize_watch_status(status: AlertStatus) -> dict:
    return {
        "symbol": status.rule.symbol,
        "last_price": status.last_price,
        "buy_below": status.rule.buy_below,
        "sell_above": status.rule.sell_above,
        "distance_to_buy_percent": status.distance_to_buy_percent,
        "state": _watch_state(status),
    }


def serialize_triggered_alert(alert: TriggeredAlert) -> dict:
    return {
        "symbol": alert.symbol,
        "direction": alert.direction,
        "last_price": alert.last_price,
        "target_price": alert.target_price,
        "headline": alert.headline,
    }


def serialize_signal(signal: Signal) -> dict:
    return {
        "symbol": signal.symbol,
        "direction": signal.direction,
        "last_price": signal.last_price,
        "average_cost": signal.average_cost,
        "discount_to_average_cost_percent": signal.discount_to_average_cost_percent,
        "severity": signal.severity,
        "conditions": [_serialize_condition(condition) for condition in signal.conditions],
        **_serialize_indicators(signal.snapshot),
        **_serialize_market_context(signal),
        "summary": narrate_signal(signal),
        "muted": signal.is_muted,
    }


def serialize_subscription(subscription) -> dict:
    return {
        "symbol": subscription.symbol,
        "sources": list(subscription.sources),
        "enabled": subscription.is_enabled,
    }


def _serialize_condition(condition) -> dict:
    return {"code": condition.name, "label": condition.label, "direction": condition.direction}


def _serialize_indicators(snapshot) -> dict:
    return {
        "rsi": snapshot.rsi,
        "sma_short": snapshot.sma_short,
        "sma_long": snapshot.sma_long,
        "bias_to_sma_short_percent": snapshot.bias_to_sma_short_percent,
        "bollinger_lower": snapshot.bands.lower if snapshot.bands else None,
        "bollinger_upper": snapshot.bands.upper if snapshot.bands else None,
    }


def _serialize_market_context(signal: Signal) -> dict:
    context = signal.market_context
    return {
        "likely_market_wide": signal.likely_market_wide,
        "benchmark_symbol": context.benchmark_symbol if context else None,
        "benchmark_change_percent": context.change_percent if context else None,
    }


def serialize_signal_thresholds(thresholds) -> dict:
    return {
        "average_cost_discount_percent": thresholds.average_cost_discount_percent,
        "short_trend_bias_percent": thresholds.short_trend_bias_percent,
        "rsi_oversold": thresholds.rsi_oversold,
        "rsi_overbought": thresholds.rsi_overbought,
    }


def serialize_resolved_thresholds(resolved) -> dict:
    return {
        "symbol": resolved.symbol,
        "source": resolved.source,
        **serialize_signal_thresholds(resolved.thresholds),
    }


def serialize_signal_event(event) -> dict:
    return {
        "event_date": event.event_date,
        "symbol": event.symbol,
        "direction": event.direction,
        "conditions": event.conditions,
        "last_price": event.last_price,
    }


def serialize_assumptions(assumptions: ValuationAssumptions) -> dict:
    return {
        "target_pe": assumptions.target_price_earnings,
        "required_return": assumptions.required_return,
        "dividend_growth": assumptions.dividend_growth,
        "margin_of_safety": assumptions.margin_of_safety,
    }


def serialize_manual_position(manual: ManualPosition) -> dict:
    return {
        "symbol": manual.symbol,
        "quantity": manual.quantity,
        "average_cost": manual.average_cost,
        "note": manual.note,
    }


def serialize_import_outcome(outcome: ImportOutcome) -> dict:
    return {
        "parsed": outcome.parsed,
        "inserted": outcome.inserted,
        "activity_breakdown": outcome.activity_breakdown,
        "warnings": [
            {
                "symbol": warning.symbol,
                "shares_implied_by_dividend": warning.shares_implied_by_dividend,
                "shares_from_imported_trades": warning.shares_from_imported_trades,
                "missing_shares": warning.missing_shares,
            }
            for warning in outcome.warnings
        ],
    }
