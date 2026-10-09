"""Register a new account."""

from __future__ import annotations

from apps.users.application.dto import RegisterUserInput
from apps.users.application.ports import PasswordHasher, PasswordPolicy, UserRepository
from apps.users.domain.entities import User, normalize_email, normalize_full_name
from apps.users.domain.enums import Role
from apps.users.domain.exceptions import EmailAlreadyTaken


class RegisterUser:
    def __init__(
        self,
        users: UserRepository,
        hasher: PasswordHasher,
        policy: PasswordPolicy,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._policy = policy

    def execute(self, data: RegisterUserInput) -> User:
        email = normalize_email(data.email)
        full_name = normalize_full_name(data.full_name)

        # Checked before hashing, so a duplicate registration does not pay for a KDF run.
        if self._users.exists_email(email):
            raise EmailAlreadyTaken()

        self._policy.validate(data.password, email=email, full_name=full_name)

        # The unique index on `email` is the real guard: two concurrent registrations both pass
        # the check above, and the repository turns the resulting IntegrityError into the same
        # EmailAlreadyTaken the caller already handles.
        return self._users.create(
            email=email,
            password_hash=self._hasher.hash(data.password),
            full_name=full_name,
            role=Role.USER,
        )
