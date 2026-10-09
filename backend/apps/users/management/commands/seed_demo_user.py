"""Create a demo account so a fresh deployment has something to log in with."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.users.domain.enums import Role
from apps.users.infrastructure.models import User


class Command(BaseCommand):
    help = "Создать демонстрационного пользователя (идемпотентно)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--email", default="demo@example.com")
        parser.add_argument("--password", default="demo-password-2026")
        parser.add_argument("--full-name", default="Демо Пользователь")
        parser.add_argument("--role", default=Role.USER.value, choices=[r.value for r in Role])

    def handle(self, *args, **options) -> None:
        email = options["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            self.stdout.write(f"Пользователь {email} уже существует — пропускаю.")
            return
        User.objects.create_user(
            email=email,
            password=options["password"],
            full_name=options["full_name"],
            role=options["role"],
        )
        self.stdout.write(self.style.SUCCESS(f"Создан пользователь {email}."))
