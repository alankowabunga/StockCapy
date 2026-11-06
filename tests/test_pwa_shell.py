import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_settings
from portfolio.web.api import create_app

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "portfolio" / "web" / "static"
MANIFEST = json.loads((STATIC_DIR / "manifest.json").read_text())


@pytest.fixture
def secured_client(tmp_path, monkeypatch):
    monkeypatch.setattr("portfolio.web.api.MarketDataClient", lambda: None)
    return TestClient(create_app(make_settings(tmp_path, api_token="a-token")))


def test_manifest_describes_an_installable_standalone_app():
    assert MANIFEST["display"] == "standalone"
    assert MANIFEST["start_url"] == "/"
    assert MANIFEST["scope"] == "/"
    assert any(icon["purpose"] == "maskable" for icon in MANIFEST["icons"])
    assert {icon["sizes"] for icon in MANIFEST["icons"]} >= {"192x192", "512x512"}


def test_declared_icons_exist_as_real_png_files():
    for icon in MANIFEST["icons"]:
        file = STATIC_DIR / icon["src"].lstrip("/")
        assert file.exists(), icon["src"]
        assert file.read_bytes()[1:4] == b"PNG"


def test_index_links_the_manifest_and_the_apple_touch_icon():
    head = (STATIC_DIR / "index.html").read_text()
    assert '<link rel="manifest" href="/manifest.json">' in head
    assert '/icons/apple-touch-icon.png' in head


def test_install_files_are_served_without_a_token(secured_client):
    """The phone fetches these before the user ever logs in."""
    for path in ("/manifest.json", "/icons/icon-192.png", "/icons/apple-touch-icon.png"):
        assert secured_client.get(path).status_code == 200, path
    assert secured_client.get("/api/holdings").status_code == 401
