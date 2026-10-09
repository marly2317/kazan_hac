"""Import the КТРУ reference from the organisers' shard archives.

    python manage.py import_ktru /data/ktru --okpd2 14 --okpd2 31 --okpd2 25.73

The full dump is ~180 archives covering every ОКПД2 group there is. ``--okpd2`` keeps only the
groups being worked on, which for this hackathon are одежда, мебель and инструменты — importing
everything costs a long load for positions the UI will never offer.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.ktru.domain.entities import Position as PositionEntity
from apps.ktru.infrastructure.models import Characteristic, PermittedValue, Position
from apps.ktru.infrastructure.xml_parser import parse_archive, parse_positions

#: Rows per bulk_create. Large enough that the round trips disappear, small enough that one
#: batch is not a hundred megabytes of parameters.
BATCH = 2_000


class Command(BaseCommand):
    help = "Загрузить справочник КТРУ из архивов (zip) или XML-файлов."

    def add_arguments(self, parser) -> None:
        parser.add_argument("source", help="Каталог с архивами КТРУ, либо один zip/xml файл.")
        parser.add_argument(
            "--okpd2",
            action="append",
            default=[],
            metavar="PREFIX",
            help="Оставить только позиции с кодом ОКПД2, начинающимся с префикса. "
            "Можно повторять. По умолчанию импортируется всё.",
        )
        parser.add_argument(
            "--limit", type=int, default=0, help="Остановиться после N позиций (для отладки)."
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Очистить справочник перед импортом.",
        )

    def handle(self, *args, **options) -> None:
        source = Path(options["source"]).expanduser()
        if not source.exists():
            raise CommandError(f"Источник не найден: {source}")

        prefixes = tuple(options["okpd2"])
        limit = options["limit"]

        if options["flush"]:
            # Cascades to characteristics and values.
            deleted, _ = Position.objects.all().delete()
            self.stdout.write(f"Удалено строк: {deleted}")

        files = self._sources(source)
        if not files:
            raise CommandError(f"В {source} не найдено ни одного zip/xml файла КТРУ.")

        self.stdout.write(f"Файлов к обработке: {len(files)}")

        seen = 0
        kept = 0
        buffer: list[PositionEntity] = []

        for path in files:
            reader = parse_archive if path.suffix == ".zip" else parse_positions
            for position in reader(path):
                seen += 1
                if prefixes and not position.okpd2_code.startswith(prefixes):
                    continue
                buffer.append(position)
                kept += 1

                if len(buffer) >= BATCH:
                    self._flush(buffer)
                    buffer.clear()
                    self.stdout.write(f"  …просмотрено {seen}, загружено {kept}")

                if limit and kept >= limit:
                    break
            if limit and kept >= limit:
                break

        if buffer:
            self._flush(buffer)

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово. Просмотрено {seen}, загружено {kept}. "
                f"В базе: позиций {Position.objects.count()}, "
                f"характеристик {Characteristic.objects.count()}, "
                f"значений {PermittedValue.objects.count()}."
            )
        )

    @staticmethod
    def _sources(source: Path) -> list[Path]:
        if source.is_file():
            return [source]
        return sorted(
            [*source.rglob("*.zip"), *source.rglob("*.xml")],
            key=lambda path: path.name,
        )

    @transaction.atomic
    def _flush(self, positions: list[PositionEntity]) -> None:
        """Write one batch.

        ``update_conflicts`` rather than a prior delete: a re-run has to be idempotent, and
        КТРУ ships the same position in more than one shard often enough that failing on a
        duplicate key would abort an otherwise fine import.
        """
        Position.objects.bulk_create(
            [
                Position(
                    code=item.code,
                    name=item.name,
                    okpd2_code=item.okpd2_code,
                    okpd2_name=item.okpd2_name,
                )
                for item in positions
            ],
            update_conflicts=True,
            update_fields=["name", "okpd2_code", "okpd2_name"],
            unique_fields=["code"],
        )

        codes = [item.code for item in positions]
        # The characteristics of a re-imported position are replaced wholesale: КТРУ may have
        # dropped one, and merging would leave the removed characteristic behind forever.
        Characteristic.objects.filter(position_id__in=codes).delete()

        rows = [
            Characteristic(
                position_id=item.code,
                code=characteristic.code,
                name=characteristic.name,
                type=characteristic.type.value,
                is_required=characteristic.is_required,
            )
            for item in positions
            for characteristic in item.characteristics
        ]
        created = Characteristic.objects.bulk_create(rows, batch_size=BATCH)

        values: list[PermittedValue] = []
        index = 0
        for item in positions:
            for characteristic in item.characteristics:
                row = created[index]
                index += 1
                for value in characteristic.values:
                    values.append(
                        PermittedValue(
                            characteristic=row,
                            kind=value.kind.value,
                            text=value.text,
                            exact=value.exact,
                            minimum=value.minimum,
                            maximum=value.maximum,
                            min_inclusive=value.min_inclusive,
                            max_inclusive=value.max_inclusive,
                            unit_code=value.unit.code if value.unit else "",
                            unit_name=value.unit.name if value.unit else "",
                        )
                    )
        PermittedValue.objects.bulk_create(values, batch_size=BATCH)
