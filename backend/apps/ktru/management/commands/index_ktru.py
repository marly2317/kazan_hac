"""Перестроить индексы OpenSearch из справочника в Postgres.

    python manage.py index_ktru

Запускается после каждого `import_ktru`: движок — производное представление справочника, и
пока команда не выполнена, поиск идёт по Postgres (см. `search/client.py`). Это не аварийный
режим, а то, что делает переезд обратимым.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.ktru.infrastructure.search.indexer import rebuild


class Command(BaseCommand):
    help = "Перестроить индексы OpenSearch (позиции и словарь подсказок) из Postgres."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--prefix",
            default="",
            metavar="PREFIX",
            help="Собрать индексы под другим префиксом имён — например, для замера рядом с "
            "рабочим индексом, не затрагивая его.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Не писать ход работы, только итог.",
        )

    def handle(self, *args, **options) -> None:
        progress = None if options["quiet"] else lambda message: self.stdout.write(message)

        try:
            written = rebuild(prefix=options["prefix"] or None, progress=progress)
        except RuntimeError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово: позиций {written['positions']}, "
                f"вхождений в словаре подсказок {written['terms']}."
            )
        )
