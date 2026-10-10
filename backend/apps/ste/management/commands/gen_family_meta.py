"""Сгенерировать LLM-метаданные семейств КТРУ (архитектура §3.7): синонимы, описание, слова.

    python manage.py gen_family_meta --dry-run              # размеры промптов, без запросов
    python manage.py gen_family_meta --batches 2            # два пакета (2 запроса)
    python manage.py gen_family_meta                        # всё недостающее (~12 запросов)
    python manage.py gen_family_meta --only F399,F219 --force

Запуск продолжает прерванный: готовые семейства пропускаются, файл сохраняется после каждого
пакета. Бесплатный аккаунт — 50 запросов в сутки на всё, поэтому сначала ``--dry-run``.
После генерации просмотрите ``kb/family_meta.json`` глазами и пересчитайте индекс поиска.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.ste import config
from apps.ste.application import family_meta as M


class Command(BaseCommand):
    help = "LLM-метаданные семейств КТРУ (синонимы, описание, ключевые слова) → family_meta.json"

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=M.DEFAULT_BATCH)
        parser.add_argument("--batches", type=int, default=None, help="не больше N пакетов")
        parser.add_argument("--dry-run", action="store_true", help="только размеры промптов")
        parser.add_argument("--force", action="store_true", help="перегенерировать готовые")
        parser.add_argument("--only", default="", help="F001,F002 или коды семейств")
        parser.add_argument("--include-generic", action="store_true", help="и общие позиции")
        parser.add_argument("--kb", default=str(config.KB_DIR), help="каталог базы знаний")
        parser.add_argument("--out", default=None, help="по умолчанию <kb>/family_meta.json")
        parser.add_argument("--brands", default=None, help="файл брендов (JSON-список/строки)")
        parser.add_argument("--timeout", type=float, default=180.0, help="таймаут запроса, с")

    def handle(self, *args, **options):
        from apps.ste.kb_build.build import load

        kb_dir = Path(options["kb"])
        try:
            kb = load(kb_dir)
        except FileNotFoundError as error:
            raise CommandError(f"Нет базы знаний в {kb_dir}: {error}") from error
        out = Path(options["out"]) if options["out"] else kb_dir / M.META_FILE

        families = kb.selectable() + (kb.generic() if options["include_generic"] else [])
        only = {item.strip() for item in options["only"].split(",") if item.strip()}
        if only:
            families = [f for f in families if f.short_id in only or f.id in only]
            if not families:
                raise CommandError(f"Ни одно семейство не подходит под --only {options['only']}")

        existing = M.load_meta(out)
        todo = M.pending(families, existing, options["force"])
        groups = M.batches(todo, options["batch_size"])
        if options["batches"] is not None:
            groups = groups[: max(0, options["batches"])]
        self.stdout.write(
            f"Семейств: {len(families)}, готово: {len(families) - len(todo)}, "
            f"к генерации: {len(todo)} → пакетов в этом запуске: {len(groups)}"
        )

        if options["dry_run"]:
            for index, group in enumerate(groups, start=1):
                size = M.prompt_size(group)
                self.stdout.write(
                    f"  пакет {index}: {group[0].short_id}…{group[-1].short_id} "
                    f"({len(group)} шт.) — {size} символов ≈ {size // 3} токенов"
                )
            return
        if not groups:
            self.stdout.write(self.style.SUCCESS("Всё уже сгенерировано."))
            return

        from apps.ste.infrastructure.llm_client import get_llm_client

        client = get_llm_client(timeout=options["timeout"])
        if client is None:
            raise CommandError("LLM выключена или нет ключа: задайте STE_LLM_API_KEY.")
        brands = M.load_brands(Path(options["brands"]) if options["brands"] else None)
        selected = [family for group in groups for family in group]
        run = M.generate_meta(
            client,
            selected,
            existing,
            batch_size=options["batch_size"],
            brands=brands,
            force=options["force"],
            on_batch=lambda meta: M.save_meta(out, meta),
        )
        self.stdout.write(
            f"Готово: {len(run.done)} семейств, HTTP-запросов: {run.requests}, "
            f"из кэша: {run.cached} · {out}"
        )
        if run.missing:
            self.stdout.write(
                self.style.WARNING(f"Без метаданных (возьмёт следующий запуск): {len(run.missing)}")
            )
        if run.stopped:
            raise CommandError(f"Остановлено: {run.stopped}")
