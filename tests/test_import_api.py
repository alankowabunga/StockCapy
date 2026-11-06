from conftest import make_settings
from fastapi.testclient import TestClient
from test_api import CHINESE_CSV, FakeMarketDataClient

from portfolio.web.api import create_app

TW_CSV = (
    "日期,代號,動作,股數,價格,手續費,稅,備註\n"
    "2026/01/15,2330.TW,買進,1000,600,855,0,\n"
)


def _api(tmp_path, monkeypatch):
    monkeypatch.setattr("portfolio.web.api.MarketDataClient", FakeMarketDataClient)
    return TestClient(create_app(make_settings(tmp_path)))


def test_formats_are_listed_by_market_with_template_links(tmp_path, monkeypatch):
    formats = _api(tmp_path, monkeypatch).get("/api/import/formats").json()
    by_key = {item["key"]: item for item in formats}
    assert by_key["firstrade"]["market"] == "US" and by_key["firstrade"]["template_url"] is None
    assert by_key["generic-tw"]["template_url"] == "/templates/generic-tw.csv"


def test_template_is_downloadable(tmp_path, monkeypatch):
    response = _api(tmp_path, monkeypatch).get("/templates/generic-tw.csv")
    assert response.status_code == 200 and "2330.TW" in response.text


def test_default_format_still_imports_firstrade(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    response = api.post("/api/import", files={"file": ("x.csv", CHINESE_CSV, "text/csv")})
    assert response.status_code == 200 and response.json()["parsed"] == 2


def test_taiwan_import_creates_a_taiwan_holding(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    response = api.post(
        "/api/import?format=generic-tw", files={"file": ("tw.csv", TW_CSV, "text/csv")}
    )
    assert response.status_code == 200
    assert response.json()["inserted"] == 1
    again = api.post("/api/import?format=generic-tw", files={"file": ("tw.csv", TW_CSV, "text/csv")})
    assert again.json()["inserted"] == 0


def test_wrong_market_file_is_refused_with_row_detail(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    response = api.post(
        "/api/import?format=generic-us", files={"file": ("tw.csv", TW_CSV, "text/csv")}
    )
    assert response.status_code == 400
    assert "第 2 列" in response.json()["detail"]


def test_unknown_format_is_a_400(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    response = api.post("/api/import?format=zzz", files={"file": ("x.csv", TW_CSV, "text/csv")})
    assert response.status_code == 400
