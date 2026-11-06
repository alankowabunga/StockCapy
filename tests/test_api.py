import pandas as pd
import pytest
from conftest import make_settings
from fastapi.testclient import TestClient

from portfolio.market import Fundamentals, PricePosition, Quote
from portfolio.notifiers import DeliveryResult
from portfolio.repository import save_price_history
from portfolio.store import open_store
from portfolio.web.api import create_app

CHINESE_CSV = (
    '"日期","交易類別","數量","說明","代號","賬戶類別","價格","金額"\n'
    '"6/15/2026","買進","5","MSFT UNSOLICITED","MSFT","融資","400.00","-2,000.00"\n'
    '"9/10/2026","股息","0","MSFT CASH DIV ON 5 SHS NON-RES TAX WITHHELD $1.50000","MSFT","融資","0.00","5.00"\n'
)

MISSING_BASIS_CSV = (
    '"日期","交易類別","數量","說明","代號","賬戶類別","價格","金額"\n'
    '"6/30/2026","股息","0","VTI CASH DIV ON 10.05 SHS","VTI","融資","0.00","15.00"\n'
)


class FakeMarketDataClient:
    """Keeps API tests off the network and deterministic."""

    def quote(self, symbol):
        return Quote(symbol=symbol, last_price=500.0, previous_close=495.0, currency="USD")

    def quotes(self, symbols):
        return {symbol: self.quote(symbol) for symbol in symbols}

    def fundamentals(self, symbol):
        return Fundamentals(
            symbol=symbol,
            values={"trailingEps": 20.0, "forwardEps": 22.0, "bookValue": 8.0,
                    "trailingPE": 25.0, "dividendRate": 3.0, "dividendYield": 0.6,
                    "targetMeanPrice": 560.0, "sector": "Technology", "quoteType": "EQUITY"},
        )

    def price_position(self, symbol):
        return PricePosition(symbol, 500.0, 350.0, 560.0, 460.0, "EQUITY")

    def dividend_history(self, symbol):
        return pd.Series(dtype="float64")

    def annual_diluted_eps(self, symbol):
        return {}

    def close_prices(self, symbol, start, end):
        return pd.Series(dtype="float64")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("portfolio.web.api.MarketDataClient", FakeMarketDataClient)
    settings = make_settings(tmp_path)
    return TestClient(create_app(settings))


def test_portfolio_without_holdings_returns_conflict(client):
    response = client.get("/api/portfolio")
    assert response.status_code == 409
    assert "持倉" in response.json()["detail"]


def test_targets_is_empty_before_any_data(client):
    assert client.get("/api/targets").json() == []


def test_import_returns_counts_and_breakdown(client):
    response = client.post("/api/import", files={"file": ("export.csv", CHINESE_CSV, "text/csv")})
    body = response.json()
    assert response.status_code == 200
    assert body["parsed"] == 2
    assert body["inserted"] == 2
    assert body["activity_breakdown"] == {"BUY": 1, "DIVIDEND": 1}
    assert body["warnings"] == []


def test_reimporting_the_same_file_inserts_nothing(client):
    files = {"file": ("export.csv", CHINESE_CSV, "text/csv")}
    client.post("/api/import", files=files)
    body = client.post("/api/import", files={"file": ("export.csv", CHINESE_CSV, "text/csv")}).json()
    assert body["parsed"] == 2
    assert body["inserted"] == 0


def test_import_surfaces_missing_cost_basis_warning(client):
    body = client.post(
        "/api/import", files={"file": ("export.csv", MISSING_BASIS_CSV, "text/csv")}
    ).json()
    assert body["warnings"][0]["symbol"] == "VTI"
    assert body["warnings"][0]["missing_shares"] == pytest.approx(10.05)


def test_import_rejects_an_unrecognizable_csv(client):
    response = client.post("/api/import", files={"file": ("junk.csv", "a,b\n1,2\n", "text/csv")})
    assert response.status_code == 400
    assert "無法識別" in response.json()["detail"]


def test_portfolio_reflects_an_imported_position(client):
    client.post("/api/import", files={"file": ("export.csv", CHINESE_CSV, "text/csv")})
    body = client.get("/api/portfolio").json()
    assert body["positions"][0]["symbol"] == "MSFT"
    assert body["positions"][0]["quantity"] == pytest.approx(5)
    assert body["summary"]["market_value"] == pytest.approx(2500.0)
    assert body["sector_exposure"][0]["sector"] == "Technology"


def test_manual_position_lifecycle(client):
    created = client.post(
        "/api/positions/manual",
        json={"symbol": "vti", "quantity": 10.05, "average_cost": 300.00, "note": "早期買進"},
    )
    assert created.json() == {"ok": True}
    listed = client.get("/api/positions/manual").json()
    assert listed[0]["symbol"] == "VTI"
    assert listed[0]["note"] == "早期買進"
    assert client.delete("/api/positions/manual/VTI").json()["removed"] is True
    assert client.get("/api/positions/manual").json() == []


def test_manual_position_overrides_the_imported_one(client):
    client.post("/api/import", files={"file": ("export.csv", CHINESE_CSV, "text/csv")})
    client.post(
        "/api/positions/manual",
        json={"symbol": "MSFT", "quantity": 20.0, "average_cost": 400.00},
    )
    positions = client.get("/api/portfolio").json()["positions"]
    assert len(positions) == 1
    assert positions[0]["quantity"] == pytest.approx(20.0)
    assert positions[0]["average_cost"] == pytest.approx(400.00)


def test_watchlist_lifecycle_and_state(client):
    assert client.get("/api/watchlist").json() == []
    client.post("/api/watchlist", json={"symbol": "MSFT", "buy_below": 520.0})
    entry = client.get("/api/watchlist").json()[0]
    assert entry["symbol"] == "MSFT"
    assert entry["state"] == "buy_triggered"
    assert entry["distance_to_buy_percent"] == pytest.approx((500 / 520 - 1) * 100)
    assert client.delete("/api/watchlist/MSFT").json()["removed"] is True


def test_watchlist_rejects_a_rule_with_no_target(client):
    response = client.post("/api/watchlist", json={"symbol": "MSFT"})
    assert response.status_code == 400


def test_partial_watch_update_keeps_the_other_target(client):
    client.post("/api/watchlist", json={"symbol": "MSFT", "buy_below": 400.0, "sell_above": 600.0})
    client.post("/api/watchlist", json={"symbol": "MSFT", "buy_below": 420.0})
    entry = client.get("/api/watchlist").json()[0]
    assert entry["buy_below"] == pytest.approx(420.0)
    assert entry["sell_above"] == pytest.approx(600.0)


def test_check_reports_triggered_alerts_then_suppresses_them(client, monkeypatch):
    monkeypatch.setattr("portfolio.service.dispatch_alerts", lambda settings, alerts: [])
    client.post("/api/watchlist", json={"symbol": "MSFT", "buy_below": 520.0})
    first = client.post("/api/check", json={"resend": False}).json()
    assert first["triggered"][0]["direction"] == "BUY"
    assert "MSFT" in first["triggered"][0]["headline"]
    assert client.post("/api/check", json={"resend": False}).json()["triggered"] == []
    assert client.post("/api/check", json={"resend": True}).json()["triggered"] != []


def test_assumption_override_lifecycle(client):
    client.post("/api/import", files={"file": ("export.csv", CHINESE_CSV, "text/csv")})
    assert client.get("/api/assumptions").json()["symbols"][0]["source"] == "default"
    client.put("/api/assumptions/MSFT", json={"target_pe": 30.0, "margin_of_safety": 0.15})
    entry = client.get("/api/assumptions").json()["symbols"][0]
    assert entry["source"] == "manual"
    assert entry["target_pe"] == pytest.approx(30.0)
    assert entry["margin_of_safety"] == pytest.approx(0.15)
    assert client.delete("/api/assumptions/MSFT").json()["removed"] is True
    assert client.get("/api/assumptions").json()["symbols"][0]["source"] == "default"


def test_assumption_update_needs_at_least_one_field(client):
    assert client.put("/api/assumptions/MSFT", json={}).status_code == 400


def test_targets_expose_estimates_and_assumption_source(client):
    client.post("/api/import", files={"file": ("export.csv", CHINESE_CSV, "text/csv")})
    target = client.get("/api/targets").json()[0]
    assert target["symbol"] == "MSFT"
    assert target["assumption_source"] == "default"
    assert target["basis"] == "估值模型"
    model_names = {estimate["model_name"] for estimate in target["estimates"]}
    assert "Target P/E" in model_names
    analyst = next(e for e in target["estimates"] if e["model_name"] == "Analyst Consensus")
    assert analyst["is_informational"] is True


def test_risk_is_null_when_history_is_unavailable(client):
    client.post("/api/import", files={"file": ("export.csv", CHINESE_CSV, "text/csv")})
    assert client.get("/api/risk").json() is None


def test_dividends_report_gross_tax_and_net(client):
    client.post("/api/import", files={"file": ("export.csv", CHINESE_CSV, "text/csv")})
    body = client.get("/api/dividends").json()
    assert body["withholding_rate"] == pytest.approx(0.30)
    receipt = body["receipts"][0]
    assert receipt["gross_amount"] == pytest.approx(5.0)
    assert receipt["withheld_amount"] == pytest.approx(1.50)
    assert receipt["net_amount"] == pytest.approx(3.50)


def test_history_is_empty_without_snapshots(client):
    assert client.get("/api/history").json() == []


def _cache_closes(tmp_path, symbol, days):
    index = pd.date_range(end=pd.Timestamp.today().normalize(), periods=days, freq="D")
    closes = pd.Series([100.0 + i for i in range(days)], index=index)
    with open_store(tmp_path / "test.sqlite3") as connection:
        save_price_history(connection, symbol, closes)


def test_prices_serialize_closes_and_the_sixty_day_line(client, tmp_path):
    _cache_closes(tmp_path, "MSFT", 70)
    body = client.get("/api/prices/msft?days=3").json()
    assert body["symbol"] == "MSFT"
    assert body["moving_average_window"] == 60
    expected = [
        day.date().isoformat()
        for day in pd.date_range(end=pd.Timestamp.today().normalize(), periods=3, freq="D")
    ]
    assert [point["date"] for point in body["points"]] == expected
    assert body["points"][-1]["close"] == pytest.approx(169.0)
    assert body["points"][-1]["moving_average"] == pytest.approx(139.5)


def test_prices_are_empty_when_nothing_was_ever_cached(client):
    assert client.get("/api/prices/NVDA").json() == {
        "symbol": "NVDA", "moving_average_window": 60, "points": []
    }


def test_prices_leave_the_moving_average_null_on_short_history(client, tmp_path):
    _cache_closes(tmp_path, "VTI", 5)
    points = client.get("/api/prices/VTI").json()["points"]
    assert len(points) == 5
    assert all(point["moving_average"] is None for point in points)


def test_api_responses_forbid_browser_caching(client):
    """A cached /api/targets froze a bad response in the UI during development."""
    response = client.get("/api/watchlist")
    assert "no-store" in response.headers["cache-control"]


def test_app_shell_must_revalidate(client):
    """A cached shell paired with a newer API silently breaks the UI."""
    response = client.get("/")
    assert response.headers.get("cache-control") == "no-cache"
    assert "no-store" not in response.headers["cache-control"]


class DroppingMarketDataClient(FakeMarketDataClient):
    """A watched symbol sits well below its 60-day line; the benchmark is flat."""

    def close_prices(self, symbol, start, end):
        index = pd.date_range(end=pd.Timestamp.today().normalize(), periods=60, freq="D")
        if symbol == "SPY":
            return pd.Series([500.0] * 60, index=index)
        return pd.Series([100.0] * 59 + [80.0], index=index)


def test_signals_is_empty_before_any_holdings_or_watchlist(client):
    assert client.get("/api/signals").json() == {"signals": [], "stale": []}


def test_signals_serializes_a_triggered_observation(tmp_path, monkeypatch):
    monkeypatch.setattr("portfolio.web.api.MarketDataClient", DroppingMarketDataClient)
    app_client = TestClient(create_app(make_settings(tmp_path)))
    app_client.post("/api/watchlist", json={"symbol": "NVDA", "buy_below": 70.0})
    body = app_client.get("/api/signals").json()
    assert body["stale"] == []
    signals = body["signals"]
    assert len(signals) == 1
    assert signals[0]["symbol"] == "NVDA"
    assert signals[0]["direction"] == "BUY"
    assert "非投資建議" in signals[0]["summary"]
    assert signals[0]["likely_market_wide"] is False


def test_signal_thresholds_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr("portfolio.web.api.MarketDataClient", DroppingMarketDataClient)
    c = TestClient(create_app(make_settings(tmp_path)))
    default = c.get("/api/signals/thresholds").json()["default"]
    assert default["rsi_oversold"] == 30.0
    assert c.put("/api/signals/thresholds/NVDA", json={"rsi_oversold": 25.0}).json()["ok"]
    entry = next(s for s in c.get("/api/signals/thresholds").json()["symbols"] if s["symbol"] == "NVDA")
    assert entry["rsi_oversold"] == 25.0 and entry["source"] == "manual"
    assert c.delete("/api/signals/thresholds/NVDA").json()["removed"] is True


def test_signal_threshold_put_needs_a_field(tmp_path, monkeypatch):
    monkeypatch.setattr("portfolio.web.api.MarketDataClient", DroppingMarketDataClient)
    c = TestClient(create_app(make_settings(tmp_path)))
    assert c.put("/api/signals/thresholds/NVDA", json={}).status_code == 400


def test_signal_check_records_history(tmp_path, monkeypatch):
    monkeypatch.setattr("portfolio.web.api.MarketDataClient", DroppingMarketDataClient)
    # History only records what a channel accepted, so stand in for one.
    monkeypatch.setattr(
        "portfolio.service.dispatch_signals",
        lambda settings, signals: [DeliveryResult("stub", True)] if signals else [],
    )
    c = TestClient(create_app(make_settings(tmp_path)))
    c.post("/api/watchlist", json={"symbol": "NVDA", "buy_below": 70.0})
    assert c.get("/api/signals/history").json() == []
    result = c.post("/api/signals/check", json={"resend": False}).json()
    assert len(result["signals"]) == 1
    history = c.get("/api/signals/history").json()
    assert len(history) == 1
    assert history[0]["symbol"] == "NVDA"


def test_target_pe_options_previews_every_choice(client):
    body = client.get("/api/targets/KO/pe-options").json()
    assert body["symbol"] == "KO"
    labels = [c["label"] for c in body["candidates"]]
    assert "保守" in labels and "中性" in labels and "成長股" in labels
    for candidate in body["candidates"]:
        assert candidate["target_pe"] > 0
        assert "tier" in candidate and "action_hint" in candidate


def test_target_pe_options_marks_exactly_one_current_choice(client):
    body = client.get("/api/targets/KO/pe-options").json()
    assert sum(1 for c in body["candidates"] if c["is_current"]) == 1


def test_picking_an_option_writes_the_override_and_changes_the_preview(client):
    before = client.get("/api/targets/KO/pe-options").json()
    picked = next(c for c in before["candidates"] if not c["is_current"])
    assert client.put("/api/assumptions/KO", json={"target_pe": picked["target_pe"]}).json()["ok"]
    after = client.get("/api/targets/KO/pe-options").json()
    now_current = next(c for c in after["candidates"] if c["is_current"])
    assert now_current["target_pe"] == pytest.approx(picked["target_pe"])
