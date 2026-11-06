import pytest
from conftest import make_settings
from fastapi.testclient import TestClient
from test_api import FakeMarketDataClient

from portfolio.web.api import create_app


class _RateClient(FakeMarketDataClient):
    """TWD 30 per USD; every quote is 500 in its own currency."""

    rate = 30.0

    def latest_exchange_rate(self, pair_symbol):
        if self.rate is None:
            raise RuntimeError("no FX")
        return self.rate


class _NoRateClient(_RateClient):
    rate = None


def _client(tmp_path, monkeypatch, fake):
    monkeypatch.setattr("portfolio.web.api.MarketDataClient", fake)
    api = TestClient(create_app(make_settings(tmp_path)))
    for symbol, shares, cost in (("AAPL", 10, 400.0), ("2330.TW", 100, 300.0)):
        response = api.post(
            "/api/positions/manual",
            json={"symbol": symbol, "quantity": shares, "average_cost": cost},
        )
        assert response.status_code == 200, response.text
    return api


def test_portfolio_totals_taiwan_and_us_together(tmp_path, monkeypatch):
    api = _client(tmp_path, monkeypatch, _RateClient)
    body = api.get("/api/portfolio").json()
    summary = body["summary"]
    # AAPL 10 x 500 = 5,000 USD; 2330 100 x 500 = 50,000 TWD = 1,666.67 USD
    assert summary["market_value"] == pytest.approx(5_000 + 50_000 / 30)
    sub = summary["market_subtotals"]
    assert sub["tw"]["market_value"] == pytest.approx(50_000)
    assert sub["market_value_twd"] == pytest.approx((5_000 + 50_000 / 30) * 30)
    weights = {p["symbol"]: p["weight_percent"] for p in body["positions"]}
    assert sum(weights.values()) == pytest.approx(100.0)


def test_portfolio_is_unavailable_not_wrong_without_an_exchange_rate(tmp_path, monkeypatch):
    api = _client(tmp_path, monkeypatch, _NoRateClient)
    response = api.get("/api/portfolio")
    assert response.status_code == 503
    assert "匯率" in response.json()["detail"]
