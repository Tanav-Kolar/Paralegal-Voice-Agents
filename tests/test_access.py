from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from app.security import access
from app.security.access import is_valid_access_token, issue_access_token

SECRET = "s" * 32


def make_client(**overrides) -> TestClient:
    settings = Settings(
        _env_file=None,
        demo_mode_enabled=True,
        demo_access_code="open-sesame",
        access_cookie_secret=SECRET,
        **overrides,
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    access._failed_logins.clear()
    return TestClient(app)


def test_login_sets_cookie_and_me_reports_authenticated() -> None:
    client = make_client()
    assert client.get("/api/v1/access/me").json() == {"enabled": True, "authenticated": False}
    assert client.post("/api/v1/access/login", json={"code": "open-sesame"}).status_code == 200
    assert client.get("/api/v1/access/me").json() == {"enabled": True, "authenticated": True}


def test_wrong_code_is_rejected_and_rate_limited() -> None:
    client = make_client()
    for _ in range(access.LOGIN_MAX_ATTEMPTS):
        assert client.post("/api/v1/access/login", json={"code": "nope"}).status_code == 401
    assert client.post("/api/v1/access/login", json={"code": "open-sesame"}).status_code == 429


def test_disabled_demo_mode_hides_login() -> None:
    settings = Settings(_env_file=None)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    response = TestClient(app).post("/api/v1/access/login", json={"code": "x"})
    assert response.status_code == 404


def test_token_expires_and_rejects_tampering() -> None:
    settings = Settings(_env_file=None, access_cookie_secret=SECRET)
    token = issue_access_token(settings, now=1000)
    assert is_valid_access_token(settings, token, now=1001)
    assert not is_valid_access_token(settings, token, now=1000 + access.ACCESS_TTL_SECONDS + 1)
    flipped = "1" if token[-1] == "0" else "0"
    assert not is_valid_access_token(settings, token[:-1] + flipped, now=1001)
    assert not is_valid_access_token(settings, None)
