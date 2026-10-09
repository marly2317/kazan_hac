"""Port implementations for the users module."""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError

from apps.users.domain.entities import User as UserEntity
from apps.users.domain.enums import Role
from apps.users.domain.exceptions import EmailAlreadyTaken, UserNotFound, WeakPassword
from apps.users.infrastructure.mappers import to_entity
from apps.users.infrastructure.models import User as UserModel
from core.infrastructure.repository import Repository


class DjangoUserRepository(Repository):
    not_found_error = UserNotFound

    def get_by_email(self, email: str) -> UserEntity | None:
        row = UserModel.objects.filter(email=email).first()
        return to_entity(row) if row else None

    def get_by_id(self, user_id: UUID) -> UserEntity | None:
        row = UserModel.objects.filter(id=user_id).first()
        return to_entity(row) if row else None

    def exists_email(self, email: str) -> bool:
        return UserModel.objects.filter(email=email).exists()

    def create(
        self, *, email: str, password_hash: str, full_name: str, role: Role
    ) -> UserEntity:
        try:
            row = UserModel.objects.create(
                email=email,
                password=password_hash,
                full_name=full_name,
                role=role.value,
            )
        except IntegrityError as exc:
            # The unique index is the authority on duplicates, not the use case's prior check:
            # two simultaneous registrations both pass that check and one lands here.
            raise EmailAlreadyTaken() from exc
        return to_entity(row)

    def get_password_hash(self, user_id: UUID) -> str:
        return self.get_or_raise(UserModel.objects.all(), id=user_id).password


class DjangoPasswordHasher:
    """Django's configured hashers behind the application port."""

    #: Hashing a throwaway password costs the same as verifying a real one, which is what keeps
    #: the unknown-email path from being measurably faster than the wrong-password path.
    _DUMMY_HASH = make_password("dummy-password-for-timing-equalization")

    def hash(self, raw_password: str) -> str:
        return make_password(raw_password)

    def verify(self, raw_password: str, password_hash: str) -> bool:
        return check_password(raw_password, password_hash)

    def dummy_verify(self, raw_password: str) -> None:
        check_password(raw_password, self._DUMMY_HASH)


class DjangoPasswordPolicy:
    """The password policy is Django's configured validators — never an ad-hoc regex."""

    def validate(self, raw_password: str, *, email: str, full_name: str) -> None:
        try:
            # An unsaved model instance, never written: Django's similarity validator reads
            # ``user._meta`` to name the field it matched, so a plain stand-in object breaks it.
            owner = UserModel(email=email, full_name=full_name)
            validate_password(raw_password, user=owner)
        except DjangoValidationError as exc:
            raise WeakPassword(details={"errors": list(exc.messages)}) from exc
