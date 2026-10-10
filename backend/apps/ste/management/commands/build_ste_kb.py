"""Пересобрать базу знаний КТРУ из выгрузки (архитектура §3, офлайн).

    python manage.py build_ste_kb /data/ktru
    python manage.py build_ste_kb ../materials/КТРУ --scope 14.,15.20,15.12,31.,25.73,28.24

Готовая база лежит в репозитории (``kb/ktru_kb.json.gz``), так что запускать это нужно только
при новой выгрузке или изменении охвата. После пересборки посчитайте векторы:
``python manage.py embed_ste_kb``.
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.ste import config
from apps.ste.kb_build.build import build, save
from apps.ste.kb_build.parse import load_positions


class Command(BaseCommand):
    help = "Собрать базу знаний КТРУ (семейства, листья, сигнатуры) из zip/xml выгрузки."

    def add_arguments(self, parser):
        parser.add_argument("source", help="каталог с архивами nsiKTRUNew_*.zip или xml")
        parser.add_argument("--scope", default=",".join(config.SCOPE_OKPD2))
        parser.add_argument("--out", default=str(config.KB_DIR))

    def handle(self, *args, **options):
        source = Path(options["source"])
        if not source.exists():
            raise CommandError(f"Нет такого пути: {source}")
        scope = tuple(item.strip() for item in options["scope"].split(",") if item.strip())
        started = time.perf_counter()
        positions = load_positions(source, scope)
        kb = build(positions, scope)
        path = save(kb, Path(options["out"]))
        roles = Counter(family.role for family in kb.families.values())
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(positions)} позиций → {roles.get('family', 0)} семейств, "
                f"{roles.get('generic', 0)} общих, {len(kb.leaves)} листьев · {path} · "
                f"{time.perf_counter() - started:.0f} с"
            )
        )
