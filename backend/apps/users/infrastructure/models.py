"""ORM models for accounts. The domain entity, not this model, is what leaves the module."""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.users.domain.enums import ROLE_CHOICES, Role


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).strip().lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("role", Role.USER.value)
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("role", Role.ADMIN.value)
        extra.setdefault("full_name", "Администратор")
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("Superuser must have is_staff and is_superuser set")
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """The account row.

    ``email`` is the username field: the product has no separate handle, and the SPA's login form
    asks for an address. A UUID primary key keeps account ids out of the guessable-integer space
    now that they appear in API payloads.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, verbose_name="почта")
    full_name = models.CharField(max_length=200, verbose_name="имя")
    role = models.CharField(
        max_length=16,
        choices=ROLE_CHOICES,
        default=Role.USER.value,
        db_index=True,
        verbose_name="роль",
    )
    is_active = models.BooleanField(default=True, verbose_name="активен")
    is_staff = models.BooleanField(default=False, verbose_name="доступ в админку")
    date_joined = models.DateTimeField(default=timezone.now, verbose_name="дата регистрации")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    objects = UserManager()

    class Meta:
        app_label = "users"
        db_table = "users_user"
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"

    def __str__(self) -> str:
        return self.email
