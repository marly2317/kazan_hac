"""The auth surface, end to end through DRF: payload in, envelope out."""

import pytest
from rest_framework.test import APIClient

from tests.conftest import DEMO_PASSWORD

LOGIN = "/api/v1/auth/login/"
REGISTER = "/api/v1/auth/register/"
LOGOUT = "/api/v1/auth/logout/"
ME = "/api/v1/auth/me/"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def test_login_opens_a_session_and_returns_the_account(client, account) -> None:
    response = client.post(
        LOGIN, {"email": "IVAN@example.com", "password": DEMO_PASSWORD}, format="json"
    )

    assert response.status_code == 200
    assert response.data["email"] == "ivan@example.com"
    assert response.data["initials"] == "ИИ"
    # The hash and the permission flags must never appear in the payload.
    assert set(response.data) == {"id", "email", "full_name", "initials", "role", "created_at"}

    assert client.get(ME).status_code == 200


def test_login_with_a_wrong_password_is_a_400_envelope(client, account) -> None:
    response = client.post(LOGIN, {"email": account.email, "password": "nope"}, format="json")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_credentials"


def test_an_unknown_email_is_indistinguishable_from_a_wrong_password(client, db) -> None:
    response = client.post(
        LOGIN, {"email": "nobody@example.com", "password": DEMO_PASSWORD}, format="json"
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_credentials"


def test_a_blocked_account_cannot_sign_in(client, account) -> None:
    account.is_active = False
    account.save(update_fields=["is_active"])

    response = client.post(
        LOGIN, {"email": account.email, "password": DEMO_PASSWORD}, format="json"
    )

    assert response.status_code == 403
    assert response.data["error"]["code"] == "user_inactive"


def test_me_without_a_session_is_refused(client, db) -> None:
    assert client.get(ME).status_code == 403


def test_logout_ends_the_session(client, account) -> None:
    client.post(LOGIN, {"email": account.email, "password": DEMO_PASSWORD}, format="json")

    assert client.post(LOGOUT).status_code == 204
    assert client.get(ME).status_code == 403


def test_logout_is_idempotent(client, db) -> None:
    assert client.post(LOGOUT).status_code == 204


def test_register_creates_an_account_and_signs_in(client, db) -> None:
    response = client.post(
        REGISTER,
        {"email": "New@Example.com", "password": DEMO_PASSWORD, "full_name": " Пётр  Петров "},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["email"] == "new@example.com"
    assert response.data["full_name"] == "Пётр Петров"
    assert response.data["role"] == "user"
    assert client.get(ME).status_code == 200


def test_register_rejects_a_duplicate_email(client, account) -> None:
    response = client.post(
        REGISTER,
        {"email": account.email.upper(), "password": DEMO_PASSWORD, "full_name": "Кто-то"},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "email_already_taken"


def test_register_rejects_a_weak_password(client, db) -> None:
    response = client.post(
        REGISTER, {"email": "weak@example.com", "password": "1234", "full_name": "Кто-то"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "weak_password"
    # The validator messages are what the registration form lists under the field.
    assert response.data["error"]["details"]["errors"]
