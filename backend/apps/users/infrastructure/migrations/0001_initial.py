"""The accounts table.

Hand-written to match ``apps.users.infrastructure.models``; ``makemigrations --check`` in CI is
what keeps the two in step.
"""

import uuid

import django.utils.timezone
from django.db import migrations, models

import apps.users.infrastructure.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("password", models.CharField(max_length=128, verbose_name="password")),
                (
                    "last_login",
                    models.DateTimeField(blank=True, null=True, verbose_name="last login"),
                ),
                (
                    "is_superuser",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Designates that this user has all permissions without "
                            "explicitly assigning them."
                        ),
                        verbose_name="superuser status",
                    ),
                ),
                ("email", models.EmailField(max_length=254, unique=True, verbose_name="почта")),
                ("full_name", models.CharField(max_length=200, verbose_name="имя")),
                (
                    "role",
                    models.CharField(
                        choices=[("user", "user"), ("admin", "admin")],
                        db_index=True,
                        default="user",
                        max_length=16,
                        verbose_name="роль",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="активен")),
                (
                    "is_staff",
                    models.BooleanField(default=False, verbose_name="доступ в админку"),
                ),
                (
                    "date_joined",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="дата регистрации"
                    ),
                ),
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text=(
                            "The groups this user belongs to. A user will get all permissions "
                            "granted to each of their groups."
                        ),
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.group",
                        verbose_name="groups",
                    ),
                ),
                (
                    "user_permissions",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Specific permissions for this user.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.permission",
                        verbose_name="user permissions",
                    ),
                ),
            ],
            options={
                "verbose_name": "пользователь",
                "verbose_name_plural": "пользователи",
                "db_table": "users_user",
            },
            managers=[
                ("objects", apps.users.infrastructure.models.UserManager()),
            ],
        ),
    ]
