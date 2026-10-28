"""Shared-access-code gate for the hosted prototype, using a signed HttpOnly cookie."""

from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from time import monotonic, time

from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings

ACCESS_COOKIE = "voice_intake_access"
ACCESS_TTL_SECONDS = 8 * 3600
LOGIN_WINDOW_SECONDS = 600
LOGIN_MAX_ATTEMPTS = 10

# In-memory per-instance limiter; use a shared store if Cloud Run scales past one instance.
_failed_logins: dict[str, list[float]] = {}


def _sign(secret: str, expires_at: int) -> str:
    return hmac_new(secret.encode(), str(expires_at).encode(), sha256).hexdigest()


def issue_access_token(settings: Settings, now: float | None = None) -> str:
    expires_at = int((time() if now is None else now) + ACCESS_TTL_SECONDS)
    return f"{expires_at}.{_sign(settings.access_cookie_secret, expires_at)}"


def is_valid_access_token(settings: Settings, token: str | None, now: float | None = None) -> bool:
    if not token or not settings.access_cookie_secret:
        return False
    expires_text, _, signature = token.partition(".")
    if not expires_text.isdigit():
        return False
    expires_at = int(expires_text)
    if expires_at < (time() if now is None else now):
        return False
    return compare_digest(signature, _sign(settings.access_cookie_secret, expires_at))


def access_code_matches(settings: Settings, code: str) -> bool:
    if not settings.demo_access_code:
        return False
    return compare_digest(code.encode(), settings.demo_access_code.encode())


def check_login_allowed(client_id: str) -> None:
    now = monotonic()
    recent = [t for t in _failed_logins.get(client_id, []) if now - t < LOGIN_WINDOW_SECONDS]
    _failed_logins[client_id] = recent
    if len(recent) >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again later."
        )


def record_failed_login(client_id: str) -> None:
    _failed_logins.setdefault(client_id, []).append(monotonic())


def demo_access_ready(settings: Settings) -> bool:
    return bool(
        settings.demo_mode_enabled
        and settings.demo_access_code
        and len(settings.access_cookie_secret) >= 32
    )


def has_access(request_cookies: dict[str, str], settings: Settings) -> bool:
    return demo_access_ready(settings) and is_valid_access_token(
        settings, request_cookies.get(ACCESS_COOKIE)
    )


def require_access(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Allow requests that carry a valid access cookie; hide the route when demo mode is off."""
    if not demo_access_ready(settings):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not has_access(request.cookies, settings):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Access code required")
