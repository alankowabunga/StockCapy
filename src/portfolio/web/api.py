from __future__ import annotations

import asyncio
import contextlib
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..assumptions import (
    GLOBAL_DEFAULT_KEY,
    AssumptionOverride,
    delete_override,
    load_all_overrides,
    load_global_default,
    save_override,
)
from ..auth import COOKIE_NAME, is_token_valid, presented_token
from ..config import Settings
from ..firstrade_csv import UnrecognizedCsvLayoutError
from ..holdings import ManualPosition
from ..market import MarketDataClient
from ..performance import compute_risk_metrics
from ..scheduler import periodic_alert_check
from ..repository import (
    delete_alert_rule,
    delete_manual_position,
    load_account_value_history,
    load_manual_position_records,
    save_alert_rule,
    save_manual_position,
)
from .. import importers
from ..performance import ExchangeRateUnavailableError
from ..service import NoPositionsError, load_performance, load_positions, resolve_for_symbols
from ..signal_config import (
    ThresholdOverride,
    delete_threshold_override,
    load_all_threshold_overrides,
    load_global_thresholds,
    resolve_thresholds,
    save_threshold_override,
)
from ..markets import InvalidSymbolError
from ..store import open_store
from ..targets import build_entry_targets
from . import serializers as ser
from .. import service

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_IMPORT_FORMAT = importers.DEFAULT_FORMAT_KEY


class WatchRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    buy_below: float | None = None
    sell_above: float | None = None


class AssumptionRequest(BaseModel):
    target_pe: float | None = None
    required_return: float | None = None
    dividend_growth: float | None = None
    margin_of_safety: float | None = None


class ManualPositionRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    quantity: float
    average_cost: float
    note: str = ""


class CheckRequest(BaseModel):
    resend: bool = False


class ThresholdRequest(BaseModel):
    average_cost_discount_percent: float | None = None
    short_trend_bias_percent: float | None = None
    rsi_oversold: float | None = None
    rsi_overbought: float | None = None


class SubscriptionRequest(BaseModel):
    enabled: bool


class LoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


# Endpoints the browser must reach before it holds a token.
PUBLIC_API_PATHS = frozenset(
    {"/api/session", "/api/login", "/api/logout", "/api/docs", "/api/openapi.json"}
)
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 90
DEFAULT_PRICE_WINDOW_DAYS = 180


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()
    resolved_settings.ensure_data_dir()
    client = MarketDataClient()
    app = FastAPI(
        title="Portfolio",
        docs_url="/api/docs",
        lifespan=_background_checks(resolved_settings, client),
    )
    _register_session_routes(app, resolved_settings)
    _register_routes(app, resolved_settings, client)
    _forbid_api_caching(app)
    _require_token(app, resolved_settings)
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _background_checks(settings: Settings, client: MarketDataClient):
    """Only runs when an interval is configured; local installs use launchd instead."""

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.check_interval_minutes <= 0:
            yield
            return
        task = asyncio.create_task(periodic_alert_check(settings, client))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return lifespan


def _require_token(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def check_token(request: Request, call_next):
        path = request.url.path
        needs_token = path.startswith("/api/") and path not in PUBLIC_API_PATHS
        if needs_token and not _request_is_authorized(request, settings):
            return JSONResponse({"detail": "需要有效的 API token"}, status_code=401)
        return await call_next(request)


def _request_is_authorized(request: Request, settings: Settings) -> bool:
    candidate = presented_token(
        request.headers.get("Authorization"), request.cookies.get(COOKIE_NAME)
    )
    return is_token_valid(settings, candidate)


def _register_session_routes(app: FastAPI, settings: Settings) -> None:
    @app.get("/api/session")
    def get_session(request: Request) -> dict:
        return {
            "authentication_required": settings.requires_authentication,
            "authenticated": _request_is_authorized(request, settings),
        }

    @app.post("/api/login")
    def login(request: LoginRequest, response: Response) -> dict:
        if not is_token_valid(settings, request.token.strip()):
            raise HTTPException(status_code=401, detail="token 不正確")
        response.set_cookie(
            COOKIE_NAME, request.token.strip(), max_age=COOKIE_MAX_AGE_SECONDS,
            httponly=True, samesite="strict",
        )
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True}


SHELL_PATHS = frozenset({"/", "/index.html"})


def _forbid_api_caching(app: FastAPI) -> None:
    """Prices must never come from cache, and a stale app shell must never outlive
    an API that has grown new fields."""

    @app.middleware("http")
    async def add_cache_headers(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        elif path in SHELL_PATHS:
            response.headers["Cache-Control"] = "no-cache"
        return response


# Each registrar below stays small on purpose: they are pure route wiring, so one
# function per resource keeps them readable rather than one long block.
def _register_routes(app: FastAPI, settings: Settings, client: MarketDataClient) -> None:
    for registrar in (
        _register_portfolio_route,
        _register_targets_route,
        _register_risk_route,
        _register_dividends_route,
        _register_history_route,
        _register_price_history_route,
        _register_target_pe_options_route,
        _register_assumption_read_route,
        _register_assumption_write_routes,
        _register_watchlist_read_route,
        _register_watchlist_write_routes,
        _register_check_route,
        _register_signals_route,
        _register_signal_threshold_read_route,
        _register_signal_threshold_write_routes,
        _register_signal_history_route,
        _register_signal_subscription_routes,
        _register_manual_position_routes,
        _register_import_route,
    ):
        registrar(app, settings, client)


def _require_performance(settings: Settings, client: MarketDataClient):
    try:
        return load_performance(settings, client)
    except NoPositionsError as error:
        raise HTTPException(status_code=409, detail="沒有任何持倉，請先匯入 CSV 或手動輸入") from error
    except ExchangeRateUnavailableError as error:
        raise HTTPException(status_code=503, detail=f"{error}，請稍後再試") from error


def _register_portfolio_route(app: FastAPI, settings: Settings, client: MarketDataClient) -> None:
    @app.get("/api/portfolio")
    def get_portfolio() -> dict:
        performance = _require_performance(settings, client)
        return ser.serialize_portfolio(performance, settings.home_currency)


def _register_targets_route(app: FastAPI, settings: Settings, client: MarketDataClient) -> None:
    @app.get("/api/targets")
    def get_targets() -> list[dict]:
        positions, _ = load_positions(settings)
        symbols = service.default_target_symbols(settings, positions)
        if not symbols:
            return []
        assumptions, provenance = resolve_for_symbols(settings, client, symbols)
        targets = build_entry_targets(client, assumptions, positions)
        return [
            ser.serialize_target(target, provenance.get(target.symbol, ("default", None)))
            for target in targets
        ]

def _register_risk_route(app: FastAPI, settings: Settings, client: MarketDataClient) -> None:
    @app.get("/api/risk")
    def get_risk(lookback_days: int = 365) -> dict | None:
        performance = _require_performance(settings, client)
        return ser.serialize_risk(compute_risk_metrics(client, performance, lookback_days))


def _register_dividends_route(app: FastAPI, settings: Settings, client: MarketDataClient) -> None:
    @app.get("/api/dividends")
    def get_dividends() -> dict:
        return ser.serialize_dividends(service.load_dividend_overview(settings, client))


def _register_assumption_read_route(
    app: FastAPI, settings: Settings, client: MarketDataClient
) -> None:
    @app.get("/api/assumptions")
    def get_assumptions() -> dict:
        positions, _ = load_positions(settings)
        with open_store(settings.database_path) as connection:
            default = load_global_default(connection)
            overrides = {o.symbol for o in load_all_overrides(connection)}
        symbols = sorted({p.symbol for p in positions} | overrides)
        return _assumption_payload(settings, client, default, symbols)

def _register_history_route(app: FastAPI, settings: Settings, client: MarketDataClient) -> None:
    @app.get("/api/history")
    def get_history() -> list[dict]:
        with open_store(settings.database_path) as connection:
            history = load_account_value_history(connection)
        return [{"date": day, "total_value": value} for day, value in history]


def _register_target_pe_options_route(
    app: FastAPI, settings: Settings, client: MarketDataClient
) -> None:
    @app.get("/api/targets/{symbol}/pe-options")
    def get_target_pe_options(symbol: str) -> dict:
        choices = service.load_target_pe_choices(settings, client, symbol)
        return ser.serialize_target_pe_choices(choices)


def _register_price_history_route(
    app: FastAPI, settings: Settings, client: MarketDataClient
) -> None:
    @app.get("/api/prices/{symbol}")
    def get_prices(symbol: str, days: int = DEFAULT_PRICE_WINDOW_DAYS) -> dict:
        points = service.load_close_history(settings, symbol, days)
        return ser.serialize_price_history(symbol, points)


def _register_assumption_write_routes(
    app: FastAPI, settings: Settings, client: MarketDataClient
) -> None:
    @app.put("/api/assumptions/{symbol}")
    def put_assumption(symbol: str, request: AssumptionRequest) -> dict:
        override = AssumptionOverride(symbol=symbol, **request.model_dump())
        if override.is_empty():
            raise HTTPException(status_code=400, detail="至少要指定一個假設欄位")
        with open_store(settings.database_path) as connection:
            save_override(connection, override)
        return {"ok": True}

    @app.delete("/api/assumptions/{symbol}")
    def reset_assumption(symbol: str) -> dict:
        with open_store(settings.database_path) as connection:
            removed = delete_override(connection, symbol)
        return {"ok": True, "removed": removed}


def _assumption_payload(
    settings: Settings, client: MarketDataClient, default, symbols: list[str]
) -> dict:
    resolved, provenance = resolve_for_symbols(settings, client, symbols)
    return {
        "default": ser.serialize_assumptions(default),
        "symbols": [
            {
                "symbol": symbol,
                **ser.serialize_assumptions(resolved[symbol]),
                "source": provenance[symbol][0],
                "historical_median_pe": provenance[symbol][1],
            }
            for symbol in symbols
        ],
    }


def _register_watchlist_read_route(
    app: FastAPI, settings: Settings, client: MarketDataClient
) -> None:
    @app.get("/api/watchlist")
    def get_watchlist() -> list[dict]:
        return [ser.serialize_watch_status(s) for s in service.load_watchlist(settings, client)]


def _register_watchlist_write_routes(
    app: FastAPI, settings: Settings, client: MarketDataClient
) -> None:
    @app.post("/api/watchlist")
    def add_watch(request: WatchRequest) -> dict:
        if request.buy_below is None and request.sell_above is None:
            raise HTTPException(status_code=400, detail="至少要給 buy_below 或 sell_above")
        symbol = _resolve_symbol_or_400(client, request.symbol)
        with open_store(settings.database_path) as connection:
            save_alert_rule(connection, symbol, request.buy_below, request.sell_above)
        return {"ok": True, "symbol": symbol}

    @app.delete("/api/watchlist/{symbol}")
    def remove_watch(symbol: str) -> dict:
        with open_store(settings.database_path) as connection:
            removed = delete_alert_rule(connection, symbol)
        return {"ok": True, "removed": removed}

def _register_check_route(app: FastAPI, settings: Settings, client: MarketDataClient) -> None:
    @app.post("/api/check")
    def run_check(request: CheckRequest) -> dict:
        outcome = service.run_alert_check(settings, client, resend=request.resend)
        return {
            "triggered": [ser.serialize_triggered_alert(a) for a in outcome.triggered],
            "notified": outcome.notified,
            "failed_channels": outcome.failed_channels,
        }


def _register_signals_route(app: FastAPI, settings: Settings, client: MarketDataClient) -> None:
    @app.get("/api/signals")
    def get_signals() -> dict:
        scan = service.scan_signals(settings, client)
        return {
            "signals": [ser.serialize_signal(s) for s in scan.signals],
            "stale": [ser.serialize_stale_signal_data(item) for item in scan.stale],
        }

    @app.post("/api/signals/check")
    def run_signals(request: CheckRequest) -> dict:
        outcome = service.run_signal_check(
            settings, client, options=service.DispatchOptions(resend=request.resend)
        )
        return {
            "signals": [ser.serialize_signal(s) for s in outcome.signals],
            "fresh": [ser.serialize_signal(s) for s in outcome.fresh],
            "notified": outcome.notified,
            "failed_channels": outcome.failed_channels,
            "stale": [ser.serialize_stale_signal_data(item) for item in outcome.stale],
        }


def _register_signal_threshold_read_route(
    app: FastAPI, settings: Settings, client: MarketDataClient
) -> None:
    @app.get("/api/signals/thresholds")
    def get_thresholds() -> dict:
        positions, _ = load_positions(settings)
        with open_store(settings.database_path) as connection:
            default = load_global_thresholds(connection)
            overrides = {o.symbol for o in load_all_threshold_overrides(connection)}
            symbols = sorted({p.symbol for p in positions} | overrides)
            resolved = [resolve_thresholds(connection, symbol) for symbol in symbols]
        return {
            "default": ser.serialize_signal_thresholds(default),
            "symbols": [ser.serialize_resolved_thresholds(entry) for entry in resolved],
        }


def _register_signal_threshold_write_routes(
    app: FastAPI, settings: Settings, client: MarketDataClient
) -> None:
    @app.put("/api/signals/thresholds/{symbol}")
    def put_threshold(symbol: str, request: ThresholdRequest) -> dict:
        override = ThresholdOverride(symbol=symbol, **request.model_dump())
        if override.is_empty():
            raise HTTPException(status_code=400, detail="至少要指定一個門檻欄位")
        with open_store(settings.database_path) as connection:
            save_threshold_override(connection, override)
        return {"ok": True}

    @app.delete("/api/signals/thresholds/{symbol}")
    def reset_threshold(symbol: str) -> dict:
        with open_store(settings.database_path) as connection:
            removed = delete_threshold_override(connection, symbol)
        return {"ok": True, "removed": removed}


def _register_signal_subscription_routes(
    app: FastAPI, settings: Settings, client: MarketDataClient
) -> None:
    @app.get("/api/signals/subscriptions")
    def get_subscriptions() -> list[dict]:
        return [ser.serialize_subscription(item) for item in service.load_signal_subscriptions(settings)]

    @app.put("/api/signals/subscriptions/{symbol}")
    def put_subscription(symbol: str, request: SubscriptionRequest) -> dict:
        try:
            result = service.set_signal_subscription(settings, symbol, request.enabled, client)
        except InvalidSymbolError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return ser.serialize_subscription(result)


def _register_signal_history_route(
    app: FastAPI, settings: Settings, client: MarketDataClient
) -> None:
    @app.get("/api/signals/history")
    def get_signal_history(symbol: str | None = None, limit: int = 50) -> list[dict]:
        events = service.load_signal_history(settings, symbol, limit)
        return [ser.serialize_signal_event(event) for event in events]


def _register_manual_position_routes(
    app: FastAPI, settings: Settings, client: MarketDataClient
) -> None:
    @app.get("/api/positions/manual")
    def get_manual_positions() -> list[dict]:
        with open_store(settings.database_path) as connection:
            records = load_manual_position_records(connection)
        return [ser.serialize_manual_position(record) for record in records]

    @app.post("/api/positions/manual")
    def add_manual_position(request: ManualPositionRequest) -> dict:
        symbol = _resolve_symbol_or_400(client, request.symbol)
        with open_store(settings.database_path) as connection:
            save_manual_position(connection, _to_manual_position(request, symbol))
        return {"ok": True, "symbol": symbol}

    @app.delete("/api/positions/manual/{symbol}")
    def remove_manual_position(symbol: str) -> dict:
        with open_store(settings.database_path) as connection:
            removed = delete_manual_position(connection, symbol)
        return {"ok": True, "removed": removed}


def _resolve_symbol_or_400(client: MarketDataClient, raw_symbol: str) -> str:
    try:
        return service.resolve_symbol(client, raw_symbol)
    except InvalidSymbolError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def _to_manual_position(request: ManualPositionRequest, symbol: str) -> ManualPosition:
    return ManualPosition(
        symbol=symbol,
        quantity=request.quantity,
        average_cost=request.average_cost,
        note=request.note,
    )

def _register_import_route(app: FastAPI, settings: Settings, client: MarketDataClient) -> None:
    @app.get("/api/import/formats")
    def list_import_formats() -> list[dict]:
        return [ser.serialize_import_format(fmt) for fmt in importers.list_formats()]

    @app.post("/api/import")
    async def import_csv(file: UploadFile = File(...), format: str = DEFAULT_IMPORT_FORMAT) -> dict:
        payload = await file.read()
        return _import_uploaded_csv(settings, payload, format)


def _import_uploaded_csv(settings: Settings, payload: bytes, format_key: str) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=True) as handle:
        handle.write(payload)
        handle.flush()
        try:
            outcome = service.import_transactions(
                settings, Path(handle.name), format_key=format_key
            )
        except importers.UnknownImportFormatError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except importers.ImportFormatError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except UnrecognizedCsvLayoutError as error:
            raise HTTPException(status_code=400, detail=f"無法識別這個 CSV 的欄位：{error}") from error
    return ser.serialize_import_outcome(outcome)
