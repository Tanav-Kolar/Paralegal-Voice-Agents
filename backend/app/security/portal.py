"""Password and session primitives for the lawyer portal."""

import base64
import hashlib
import hmac
import json
import os
from time import monotonic, time

from fastapi import Depends, HTTPException, status
from starlette.requests import HTTPConnection

from app.core.config import Settings, get_settings
from app.db.mongodb import get_portal_users_collection

PORTAL_COOKIE = "lawyer_portal_session"
SESSION_SECONDS = 8 * 3600
LOGIN_WINDOW_SECONDS = 600
LOGIN_MAX_ATTEMPTS = 10

# In-memory per-instance limiter; use a shared store if Cloud Run scales past one instance.
_failed_logins: dict[str, list[float]] = {}


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


ROLES = {"admin", "lawyer", "reviewer", "demo_lawyer"}


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256:600000:{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt, expected = stored.split(":")
        if algorithm != "pbkdf2_sha256" or int(rounds) < 600_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def portal_ready(settings: Settings) -> bool:
    return len(settings.portal_session_secret) >= 32


def issue_session(settings: Settings, email: str) -> str:
    payload = {"email": email, "exp": int(time()) + SESSION_SECONDS}
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        settings.portal_session_secret.encode(), encoded.encode(), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


def session_email(settings: Settings, token: str | None) -> str | None:
    if not portal_ready(settings) or not token or len(token) > 4096:
        return None
    try:
        encoded, signature = token.split(".", 1)
        actual = hmac.new(
            settings.portal_session_secret.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(actual, signature):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if int(payload["exp"]) < time():
            return None
        return str(payload["email"])
    except (ValueError, KeyError, TypeError):
        return None


async def current_user(
    request: HTTPConnection,
    settings: Settings = Depends(get_settings),
    users=Depends(get_portal_users_collection),
) -> dict:
    email = session_email(settings, request.cookies.get(PORTAL_COOKIE))
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to the lawyer portal")
    user = await users.find_one({"email": email, "active": True})
    if not user or user.get("role") not in ROLES:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session is no longer valid")
    return user


def require_role(*roles: str):
    async def dependency(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot perform this action")
        return user

    return dependency
