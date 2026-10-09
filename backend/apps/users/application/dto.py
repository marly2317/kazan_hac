"""Input dataclasses for the user use cases."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class RegisterUserInput:
    email: str
    password: str
    full_name: str


@dataclass(frozen=True)
class AuthenticateUserInput:
    email: str
    password: str


@dataclass(frozen=True)
class GetCurrentUserInput:
    user_id: UUID
