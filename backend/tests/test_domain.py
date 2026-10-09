"""The domain layer, exercised without Django: no `db` fixture, no ORM, no HTTP."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from apps.users.domain.entities import User, normalize_email, normalize_full_name
from apps.users.domain.enums import Role
from apps.users.domain.exceptions import InvalidEmail, InvalidFullName


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Ivan@Example.COM ", "ivan@example.com"),
        ("a.b+tag@sub.example.org", "a.b+tag@sub.example.org"),
    ],
)
def test_normalize_email_trims_and_lowercases(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "no-at-sign", "two@@at.com", "trailing@dot.", "a@b"])
def test_normalize_email_rejects_malformed(raw: str) -> None:
    with pytest.raises(InvalidEmail):
        normalize_email(raw)


def test_normalize_email_rejects_overlong_address() -> None:
    with pytest.raises(InvalidEmail):
        normalize_email("a" * 250 + "@example.com")


def test_normalize_full_name_collapses_whitespace() -> None:
    assert normalize_full_name("  Иван   Иванов  ") == "Иван Иванов"


@pytest.mark.parametrize("raw", ["", "   ", "\n\t"])
def test_normalize_full_name_rejects_empty(raw: str) -> None:
    with pytest.raises(InvalidFullName):
        normalize_full_name(raw)


def _user(full_name: str, email: str = "ivan@example.com") -> User:
    return User(
        id=uuid4(),
        email=email,
        full_name=full_name,
        role=Role.USER,
        is_active=True,
        created_at=datetime.now(tz=UTC),
    )


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("Иван Иванов", "ИИ"),
        ("Иван Петрович Иванов", "ИП"),  # two letters at most
        ("Иван", "И"),
    ],
)
def test_initials(full_name: str, expected: str) -> None:
    assert _user(full_name).initials == expected
