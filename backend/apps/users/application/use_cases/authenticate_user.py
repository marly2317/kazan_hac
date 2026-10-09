"""Authenticate by email and password."""

from __future__ import annotations

from apps.users.application.dto import AuthenticateUserInput
from apps.users.application.ports import PasswordHasher, UserRepository
from apps.users.domain.entities import User, normalize_email
from apps.users.domain.exceptions import InvalidCredentials, InvalidEmail, UserInactive


class AuthenticateUser:
    def __init__(self, users: UserRepository, hasher: PasswordHasher) -> None:
        self._users = users
        self._hasher = hasher

    def execute(self, data: AuthenticateUserInput) -> User:
        try:
            email = normalize_email(data.email)
        except InvalidEmail as exc:
            # A malformed email must fail exactly like a wrong password: telling the caller the
            # address "does not exist" would leak which addresses do.
            raise InvalidCredentials() from exc

        user = self._users.get_by_email(email)
        if user is None:
            # Hash anyway, so an unknown email costs the same time as a known one.
            self._hasher.dummy_verify(data.password)
            raise InvalidCredentials()

        if not self._hasher.verify(data.password, self._users.get_password_hash(user.id)):
            raise InvalidCredentials()

        if not user.is_active:
            raise UserInactive()

        return user
