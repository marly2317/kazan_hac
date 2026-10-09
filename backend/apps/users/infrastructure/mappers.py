"""ORM row ↔ domain entity translation."""

from __future__ import annotations

from apps.users.domain.entities import User as UserEntity
from apps.users.domain.enums import Role
from apps.users.infrastructure.models import User as UserModel


def to_entity(row: UserModel) -> UserEntity:
    return UserEntity(
        id=row.id,
        email=row.email,
        full_name=row.full_name,
        role=Role(row.role),
        is_active=row.is_active,
        created_at=row.date_joined,
    )
