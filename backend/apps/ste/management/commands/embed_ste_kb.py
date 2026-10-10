"""Посчитать векторы карточек семейств для поиска (архитектура §3.9, офлайн).

    python manage.py embed_ste_kb
    python manage.py embed_ste_kb --model /models/qwen3-embedding-0.6b

Запускать после пересборки базы знаний (``build_ste_kb``) и после генерации LLM-метаданных
(``gen_family_meta``): карточки меняются, и векторы нужно пересчитать. Без Django то же самое
делает ``python -m apps.ste.kb_build.embed``.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.ste import config
from apps.ste.kb_build.embed import VECTORS_FILE, run


class Command(BaseCommand):
    help = "Векторы полных карточек семейств КТРУ → family_vectors.npy + family_vectors.json"

    def add_arguments(self, parser):
        parser.add_argument("--kb", default=str(config.KB_DIR), help="каталог базы знаний")
        parser.add_argument("--out", default=None, help="куда писать (по умолчанию — в --kb)")
        parser.add_argument("--model", default=None, help="путь к модели (иначе из конфига)")

    def handle(self, *args, **options):
        embedder = None
        if options["model"]:
            from apps.ste.infrastructure.embedder import Embedder

            embedder = Embedder(options["model"], True)
        kb_dir = Path(options["kb"])
        out = Path(options["out"]) if options["out"] else kb_dir
        try:
            vectors, seconds = run(kb_dir, out, embedder)
        except (FileNotFoundError, RuntimeError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(vectors.ids)} карточек → {vectors.matrix.shape[0]}×{vectors.dim} "
                f"({vectors.model}) · {seconds:.0f} с · {out / VECTORS_FILE}"
            )
        )
