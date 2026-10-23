import pytest

from app.security import access


@pytest.fixture(autouse=True)
def reset_login_rate_limit() -> None:
    access._failed_logins.clear()
