"""Сравнение способов поиска позиций КТРУ на одних и тех же запросах.

Нужен, чтобы выбор поисковой технологии опирался на цифры, а не на ощущение. Вопрос, ради
которого команда написана, звучал так: «не взять ли OpenSearch, там же всё для поиска, и
дальше понадобится семантика?» — ответить на него можно только замером на своих данных.

    python manage.py bench_retrieval
    python manage.py bench_retrieval --backend trgm_sum --verbose

Запросы разложены по четырём корзинам, и это главное в замере: разные корзины ломаются
по-разному, и технология, выигрывающая в одной, может проигрывать в другой.

* ``lexical``     — слово есть в наименовании. Должно работать и сейчас.
* ``morphology``  — словоформа, порядок слов, составное слово: «шлифовальная машина» против
                    «Шлифмашина». Лечится морфологическим анализатором.
* ``synonym``     — народное название: «болгарка». Ни морфология, ни триграммы не помогут —
                    нужен словарь синонимов либо семантика.
* ``absent``      — товара в справочнике нет, правильный ответ «не нашли».

Корзина ``absent`` важнее остальных. Любой поиск всегда возвращает ближайшую строку, поэтому
метрика здесь не «нашёл ли», а **«обманет ли порог уверенности»**: пройдёт ли мусор в ответ
как настоящий код. Семантический поиск опасен именно здесь — он возвращает правдоподобных
соседей на что угодно.
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict

from django.core.management.base import BaseCommand

from apps.autofill.domain.matching import NAME_OVERLAP_FLOOR, name_overlap

#: ``(запрос, ожидаемое наименование либо None, корзина)``. ``None`` — должны воздержаться.
#: Все ожидаемые наименования проверяются на существование перед запуском: набор, который
#: спрашивает несуществующие позиции, меряет не поиск.
QUERIES: list[tuple[str, str | None, str]] = [
    # --- слово есть в наименовании ---
    ("молоток слесарный", "Молоток", "lexical"),
    ("куртка зимняя из кожи", "Куртка зимняя из кожи", "lexical"),
    ("стол письменный", "Стол письменный", "lexical"),
    ("шкаф для одежды металлический", "Шкаф для одежды металлический", "lexical"),
    ("стеллаж офисный", "Стеллаж офисный", "lexical"),
    ("кресло офисное", "Кресло офисное", "lexical"),
    ("ветровка мужская", "Ветровка", "lexical"),
    ("пила ручная по дереву", "Пила ручная", "lexical"),
    ("топор плотницкий", "Топор", "lexical"),
    ("кроссовки мужские", "Кроссовки", "lexical"),
    # --- словоформа, порядок слов, составное слово ---
    ("шлифовальная машина угловая", "Шлифмашина", "morphology"),
    ("машина шлифовальная", "Шлифмашина", "morphology"),
    ("гладильная доска", "Доска гладильная", "morphology"),
    ("компьютерное кресло детское", "Кресло компьютерное детское", "morphology"),
    ("стол журнальный", "Журнальный стол", "morphology"),
    ("электрическая дрель", "Дрель ручная электрическая", "morphology"),
    ("клеевой пистолет", "Пистолет клеевой электрический", "morphology"),
    ("торцовочная пила", "Пила торцовочная", "morphology"),
    ("шкафы металлические для одежды", "Шкаф для одежды металлический", "morphology"),
    ("рукоятка молотка", "Молоток", "morphology"),
    # --- народные названия ---
    ("болгарка 125 мм", "Шлифмашина", "synonym"),
    ("шуруповёрт аккумуляторный", "Шуруповерт", "synonym"),
    ("лобзик электрический", "Электролобзик", "synonym"),
    ("пассатижи комбинированные", "Пассатижи", "synonym"),
    ("стол для компьютера", "Стол компьютерный", "synonym"),
    # --- в справочнике нет: ожидается воздержание ---
    ("отвертка крестовая PH2", None, "absent"),
    ("перфоратор аккумуляторный", None, "absent"),
    ("кушетка медицинская", None, "absent"),
    ("квадрокоптер с камерой", None, "absent"),
    ("сварочный инвертор 200 А", None, "absent"),
]

SHOWN = 5


class Command(BaseCommand):
    help = "Сравнить способы поиска позиций КТРУ на одном наборе запросов."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--backend", action="append", default=[], help="Только эти способы. Можно повторять."
        )
        parser.add_argument("--repeat", type=int, default=5, help="Замеров времени на запрос.")
        parser.add_argument("--verbose", action="store_true", help="Печатать каждый запрос.")

    def handle(self, *args, **options) -> None:
        from apps.autofill.infrastructure import backends

        available = backends.all_backends()
        wanted = options["backend"] or list(available)
        missing = [name for name in wanted if name not in available]
        if missing:
            self.stderr.write(f"Нет таких способов: {', '.join(missing)}")
            self.stderr.write(f"Доступны: {', '.join(available)}")
            return

        self._check_ground_truth()

        for name in wanted:
            backend = available[name]
            if not backend.available():
                self.stdout.write(f"\n{name}: недоступен ({backend.why_not()}) — пропускаю")
                continue
            self._run(name, backend, options["repeat"], options["verbose"])

    def _check_ground_truth(self) -> None:
        """Ожидаемые наименования обязаны существовать, иначе набор меряет не поиск."""
        from apps.ktru.infrastructure.models import Position

        expected = {name for _, name, _ in QUERIES if name}
        present = set(
            Position.objects.filter(name__in=expected).values_list("name", flat=True)
        )
        unknown = sorted(expected - present)
        if unknown:
            self.stderr.write(
                self.style.WARNING(
                    "В справочнике нет ожидаемых наименований — проверьте набор: "
                    + ", ".join(unknown)
                )
            )

    def _run(self, label: str, backend, repeat: int, verbose: bool) -> None:
        hits1: dict[str, int] = defaultdict(int)
        hits5: dict[str, int] = defaultdict(int)
        total: dict[str, int] = defaultdict(int)
        timings: list[float] = []
        fooled: list[tuple[str, str, float]] = []

        for query, expected, bucket in QUERIES:
            backend.search(query, SHOWN)  # прогрев, чтобы в замер не попал первый план запроса
            samples = []
            for _ in range(repeat):
                started = time.perf_counter()
                found = backend.search(query, SHOWN)
                samples.append(time.perf_counter() - started)
            timings.append(statistics.median(samples))

            names = [name for _, name, _ in found]
            total[bucket] += 1

            if expected is None:
                # Метрика воздержания: обманет ли лидер порог уверенности. Порог один и тот же
                # для всех способов, поэтому сравнение честное.
                top = names[0] if names else ""
                overlap = name_overlap(query, top) if top else 0.0
                if overlap >= NAME_OVERLAP_FLOOR:
                    fooled.append((query, top, overlap))
                else:
                    hits1[bucket] += 1
                    hits5[bucket] += 1
                mark = "✗ пропустил" if overlap >= NAME_OVERLAP_FLOOR else "✓ воздержался"
                detail = f"{mark} (лидер «{top[:34]}», overlap {overlap:.2f})"
            else:
                if names and names[0] == expected:
                    hits1[bucket] += 1
                    hits5[bucket] += 1
                    detail = "✓"
                elif expected in names:
                    hits5[bucket] += 1
                    detail = f"~ в топ-{SHOWN}, лидер «{names[0][:34]}»"
                else:
                    detail = f"✗ «{names[0][:34] if names else '—'}»"

            if verbose:
                self.stdout.write(f"  {bucket:11} {detail:52} {query}")

        self._report(label, hits1, hits5, total, timings, fooled)

    def _report(self, label, hits1, hits5, total, timings, fooled) -> None:
        self.stdout.write("")
        self.stdout.write("=" * 74)
        self.stdout.write(self.style.MIGRATE_HEADING(f"  {label}"))
        self.stdout.write("-" * 74)
        self.stdout.write(f"  {'корзина':14}{'@1':>10}{'@5':>10}")
        for bucket in ("lexical", "morphology", "synonym", "absent"):
            if not total[bucket]:
                continue
            one = f"{hits1[bucket]}/{total[bucket]}"
            five = f"{hits5[bucket]}/{total[bucket]}"
            share = hits1[bucket] / total[bucket]
            self.stdout.write(f"  {bucket:14}{one:>10}{five:>10}   {share:.0%}")
        overall1 = sum(hits1.values())
        overall5 = sum(hits5.values())
        count = sum(total.values())
        self.stdout.write("-" * 74)
        self.stdout.write(
            f"  {'ИТОГО':14}{f'{overall1}/{count}':>10}{f'{overall5}/{count}':>10}"
            f"   {overall1 / count:.0%}"
        )
        self.stdout.write(
            f"  время поиска: медиана {statistics.median(timings) * 1000:.1f} мс, "
            f"максимум {max(timings) * 1000:.1f} мс"
        )
        if fooled:
            self.stdout.write(
                self.style.ERROR(f"  порог уверенности обманут на {len(fooled)} запросах:")
            )
            for query, top, overlap in fooled:
                self.stdout.write(f"    «{query}» → «{top}» (overlap {overlap:.2f})")
        self.stdout.write("=" * 74)
