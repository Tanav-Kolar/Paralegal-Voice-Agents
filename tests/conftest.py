import pytest

from app.security import portal


@pytest.fixture(autouse=True)
def reset_login_rate_limit() -> None:
    portal._failed_logins.clear()
