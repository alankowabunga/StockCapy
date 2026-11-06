import pytest
from fastapi.testclient import TestClient

from portfolio.auth import (
    InsecureExposureError,
    is_token_valid,
    presented_token,
    require_secure_binding,
)
from conftest import make_settings
from portfolio.config import generate_api_token, is_local_host
from portfolio.web.api import create_app

TOKEN = "correct-horse-battery-staple"


def _settings(tmp_path, api_token: str = ""):
    return make_settings(tmp_path, api_token=api_token)


@pytest.fixture
def secured_client(tmp_path, monkeypatch):
    monkeypatch.setattr("portfolio.web.api.MarketDataClient", lambda: None)
    return TestClient(create_app(_settings(tmp_path, TOKEN)))


def test_bearer_header_is_preferred_over_cookie():
    assert presented_token("Bearer abc", "cookie-value") == "abc"


def test_cookie_is_used_when_no_header_is_present():
    assert presented_token(None, "cookie-value") == "cookie-value"


def test_malformed_authorization_header_falls_back_to_cookie():
    assert presented_token("Basic abc", "cookie-value") == "cookie-value"


def test_missing_credentials_yield_an_empty_token():
    assert presented_token(None, None) == ""


def test_any_token_passes_when_authentication_is_disabled(tmp_path):
    open_settings = _settings(tmp_path)
    assert is_token_valid(open_settings, "")
    assert is_token_valid(open_settings, "anything")


def test_only_the_exact_token_passes_when_enabled(tmp_path):
    secured = _settings(tmp_path, TOKEN)
    assert is_token_valid(secured, TOKEN)
    assert not is_token_valid(secured, TOKEN + "x")
    assert not is_token_valid(secured, "")


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_local_binds_need_no_token(tmp_path, host):
    assert is_local_host(host)
    require_secure_binding(_settings(tmp_path), host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_non_local_bind_without_a_token_is_refused(tmp_path, host):
    """Exposing holdings and cost basis unauthenticated must fail closed."""
    with pytest.raises(InsecureExposureError) as error:
        require_secure_binding(_settings(tmp_path), host)
    assert "PORTFOLIO_API_TOKEN" in str(error.value)


def test_non_local_bind_is_allowed_once_a_token_exists(tmp_path):
    require_secure_binding(_settings(tmp_path, TOKEN), "0.0.0.0")


def test_generated_tokens_are_unique_and_long():
    first, second = generate_api_token(), generate_api_token()
    assert first != second
    assert len(first) >= 40


def test_protected_endpoint_rejects_anonymous_requests(secured_client):
    response = secured_client.get("/api/watchlist")
    assert response.status_code == 401
    assert "token" in response.json()["detail"]


def test_protected_endpoint_rejects_a_wrong_token(secured_client):
    response = secured_client.get("/api/watchlist", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_protected_endpoint_accepts_the_bearer_token(secured_client):
    response = secured_client.get(
        "/api/watchlist", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 200


def test_price_history_endpoint_is_protected(secured_client):
    assert secured_client.get("/api/prices/MSFT").status_code == 401


def test_session_endpoint_is_public_and_reports_state(secured_client):
    body = secured_client.get("/api/session").json()
    assert body == {"authentication_required": True, "authenticated": False}


def test_login_with_a_wrong_token_is_rejected(secured_client):
    assert secured_client.post("/api/login", json={"token": "nope"}).status_code == 401


def test_login_sets_a_cookie_that_authorizes_later_requests(secured_client):
    assert secured_client.post("/api/login", json={"token": TOKEN}).status_code == 200
    assert secured_client.get("/api/watchlist").status_code == 200
    assert secured_client.get("/api/session").json()["authenticated"] is True


def test_logout_clears_the_cookie(secured_client):
    secured_client.post("/api/login", json={"token": TOKEN})
    secured_client.post("/api/logout")
    assert secured_client.get("/api/watchlist").status_code == 401


def test_write_endpoints_are_protected_too(secured_client):
    response = secured_client.post("/api/watchlist", json={"symbol": "MSFT", "buy_below": 1.0})
    assert response.status_code == 401


def test_import_endpoint_is_protected(secured_client):
    response = secured_client.post("/api/import", files={"file": ("a.csv", "x,y\n", "text/csv")})
    assert response.status_code == 401


def test_open_server_still_serves_without_credentials(tmp_path, monkeypatch):
    """Local-only use must stay frictionless."""
    monkeypatch.setattr("portfolio.web.api.MarketDataClient", lambda: None)
    client = TestClient(create_app(_settings(tmp_path)))
    assert client.get("/api/watchlist").status_code == 200
    assert client.get("/api/session").json()["authentication_required"] is False


def test_env_file_populates_missing_variables(tmp_path, monkeypatch):
    from portfolio.config import load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text('PORTFOLIO_API_TOKEN=from-file\n# comment\n\nOTHER="quoted"\n')
    monkeypatch.delenv("PORTFOLIO_API_TOKEN", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    load_env_file(env_file)
    import os

    assert os.environ["PORTFOLIO_API_TOKEN"] == "from-file"
    assert os.environ["OTHER"] == "quoted"


def test_real_environment_wins_over_the_env_file(tmp_path, monkeypatch):
    """A shell-exported token must not be silently replaced by a stale file."""
    from portfolio.config import load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text("PORTFOLIO_API_TOKEN=from-file\n")
    monkeypatch.setenv("PORTFOLIO_API_TOKEN", "from-shell")
    load_env_file(env_file)
    import os

    assert os.environ["PORTFOLIO_API_TOKEN"] == "from-shell"


def test_missing_env_file_is_not_an_error(tmp_path):
    from portfolio.config import load_env_file

    load_env_file(tmp_path / "nope.env")


def test_api_does_not_grant_cross_origin_reads(secured_client):
    """The PWA is same-origin; a wildcard CORS header would let any site read the API."""
    response = secured_client.get(
        "/api/session", headers={"Origin": "https://evil.example"}
    )
    assert "access-control-allow-origin" not in response.headers
