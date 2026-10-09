"""DRF serializers for the auth surface.

Never expose ``is_staff``, ``is_superuser`` or the password hash: the account screen shows what
the person already knows about themselves, and the permission flags are not part of that.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.users.domain.entities import MAX_EMAIL_LENGTH, MAX_FULL_NAME_LENGTH, User
from apps.users.domain.enums import ROLE_CHOICES

#: Django truncates nothing and hashes the whole input, so an unbounded password field is a cheap
#: way to make a KDF run expensive. 128 is Django's own form limit.
MAX_PASSWORD_LENGTH = 128


class UserSerializer(serializers.Serializer):
    """The account payload — the single shape every auth endpoint answers with."""

    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    initials = serializers.CharField(read_only=True)
    role = serializers.ChoiceField(choices=ROLE_CHOICES, read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    @staticmethod
    def from_entity(user: User) -> dict[str, Any]:
        return {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "initials": user.initials,
            "role": user.role.value,
            "created_at": user.created_at,
        }


class LoginSerializer(serializers.Serializer):
    # `CharField`, not `EmailField`: the domain owns what a valid address is, and a serializer
    # rejecting the shape first would answer "invalid email" where login must answer
    # "invalid credentials".
    email = serializers.CharField(max_length=MAX_EMAIL_LENGTH)
    password = serializers.CharField(
        max_length=MAX_PASSWORD_LENGTH, write_only=True, trim_whitespace=False
    )


class RegisterSerializer(LoginSerializer):
    full_name = serializers.CharField(max_length=MAX_FULL_NAME_LENGTH)
