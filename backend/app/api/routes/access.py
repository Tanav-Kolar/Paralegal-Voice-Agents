from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.security.access import (
    ACCESS_COOKIE,
    ACCESS_TTL_SECONDS,
    access_code_matches,
    check_login_allowed,
    demo_access_ready,
    has_access,
    issue_access_token,
    record_failed_login,
)

router = APIRouter()


class LoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=200)


def _require_demo_mode(settings: Settings) -> None:
    if not demo_access_ready(settings):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    _require_demo_mode(settings)
    client_id = request.client.host if request.client else "unknown"
    check_login_allowed(client_id)
    if not access_code_matches(settings, body.code):
        record_failed_login(client_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That access code is not valid.")
    response.set_cookie(
        ACCESS_COOKIE,
        issue_access_token(settings),
        max_age=ACCESS_TTL_SECONDS,
        httponly=True,
        secure=settings.app_env != "development",
        samesite="lax",
    )
    return {"authenticated": True}


@router.get("/me")
async def me(request: Request, settings: Settings = Depends(get_settings)) -> dict[str, bool]:
    return {
        "enabled": demo_access_ready(settings),
        "authenticated": has_access(request.cookies, settings),
    }


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(ACCESS_COOKIE)
    return {"authenticated": False}
