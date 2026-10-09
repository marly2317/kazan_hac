"""Ports the user use cases depend on. Implementations live in ``infrastructure``."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from apps.users.domain.entities import User
from apps.users.domain.enums import Role


class UserRepository(Protocol):
    def get_by_email(self, email: str) -> User | None: ...

    def get_by_id(self, user_id: UUID) -> User | None: ...

    def exists_email(self, email: str) -> bool: ...

    def create(self, *, email: str, password_hash: str, full_name: str, role: Role) -> User: ...

    def get_password_hash(self, user_id: UUID) -> str: ...


class PasswordHasher(Protocol):
    """Hashing stays with Django's hashers, but behind a port so the use case has no import."""

    def hash(self, raw_password: str) -> str: ...

    def verify(self, raw_password: str, password_hash: str) -> bool: ...

    def dummy_verify(self, raw_password: str) -> None:
        """Burn the same work as a real verification, to equalize timing for unknown emails."""
        ...


class PasswordPolicy(Protocol):
    def validate(self, raw_password: str, *, email: str, full_name: str) -> None:
        """Raise ``WeakPassword`` when the password fails the configured validators."""
        ...
