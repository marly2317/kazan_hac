import pytest

from apps.users.infrastructure.models import User

DEMO_PASSWORD = "correct-horse-battery-2026"


@pytest.fixture
def account(db) -> User:
    return User.objects.create_user(
        email="ivan@example.com",
        password=DEMO_PASSWORD,
        full_name="Иван Иванов",
    )
