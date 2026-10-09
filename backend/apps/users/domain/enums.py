"""Roles. Any module needing a role check imports this enum, never a string literal."""

from enum import StrEnum


class Role(StrEnum):
    USER = "user"
    ADMIN = "admin"

    @property
    def is_staff_side(self) -> bool:
        return self is Role.ADMIN


#: Choice pairs for serializers and for ``ENUM_NAME_OVERRIDES``, so the generated schema names
#: this enum ``RoleEnum`` once instead of ``Enum1``, ``Enum2``… per serializer repeating them.
ROLE_CHOICES = [(role.value, role.value) for role in Role]
