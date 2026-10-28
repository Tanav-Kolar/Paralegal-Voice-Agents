"""Named-user authentication for the lawyer portal."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.db.mongodb import get_portal_users_collection
from app.security.access import check_login_allowed, record_failed_login
from app.security.portal import (
    PORTAL_COOKIE,
    SESSION_SECONDS,
    current_user,
    issue_session,
    portal_ready,
    verify_password,
)

router = APIRouter()


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


def public_user(user: dict) -> dict:
    return {"email": user["email"], "name": user["name"], "role": user["role"]}


@router.post("/login")
async def login(
    body: Credentials,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    users=Depends(get_portal_users_collection),
) -> dict:
    if not portal_ready(settings):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Portal sign-in is not configured")
    client_id = request.client.host if request.client else "unknown"
    check_login_allowed(client_id)
    email = body.email.strip().lower()
    user = await users.find_one({"email": email, "active": True})
    if not user or not verify_password(body.password, user.get("password_hash", "")):
        record_failed_login(client_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")
    response.set_cookie(
        PORTAL_COOKIE,
        issue_session(settings, email),
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=settings.app_env != "development",
        samesite="lax",
    )
    return public_user(user)


@router.get("/me")
async def me(user: dict = Depends(current_user)) -> dict:
    return public_user(user)


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(PORTAL_COOKIE)
    return {"authenticated": False}
