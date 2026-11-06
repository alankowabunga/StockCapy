from __future__ import annotations

import secrets

from .config import Settings, is_local_host

COOKIE_NAME = "portfolio_token"
BEARER_PREFIX = "Bearer "


class InsecureExposureError(RuntimeError):
    """Raised when a non-local bind is attempted without an API token."""


def presented_token(authorization: str | None, cookie: str | None) -> str:
    """Bearer header for API clients, cookie for the browser."""
    if authorization and authorization.startswith(BEARER_PREFIX):
        return authorization[len(BEARER_PREFIX) :].strip()
    return (cookie or "").strip()


def is_token_valid(settings: Settings, candidate: str) -> bool:
    if not settings.requires_authentication:
        return True
    return bool(candidate) and secrets.compare_digest(candidate, settings.api_token)


def require_secure_binding(settings: Settings, host: str) -> None:
    """Fails closed: a token is mandatory the moment the server leaves localhost."""
    if is_local_host(host) or settings.requires_authentication:
        return
    raise InsecureExposureError(
        f"綁定 {host} 會讓其他裝置連進來，但沒有設定 PORTFOLIO_API_TOKEN。"
        "先跑 `portfolio token --write-env` 產生 token，或改回 --host 127.0.0.1。"
    )
