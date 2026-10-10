"""Индекс подсказок: весь словарь КТРУ в памяти процесса.

Замер, на котором основано решение не ставить отдельный поисковый движок: словарь — 2638 фраз,
50 КБ текста, префиксный поиск по отсортированному списку занимает **1 мкс**. Любой внешний
сервис подсказок, как бы быстро он ни искал, сначала стоит сетевого обращения, то есть на три
порядка дороже самого поиска. Даже на полном справочнике (60 тысяч позиций) это пара мегабайт.

Структура — отсортированный список слов плюс `bisect`, а не префиксное дерево: на таком объёме
они неразличимы по скорости, а список втрое меньше по памяти и его очевидно видно в отладке.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter, defaultdict

from apps.autofill.domain.suggest import Suggestion, SuggestionKind
from apps.ktru.infrastructure.models import Characteristic, PermittedValue, Position

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[а-яёa-z0-9]+(?:-[а-яёa-z0-9]+)*", re.IGNORECASE)

#: Слова короче этого не подсказываются: предлоги и обрывки только мешают.
MIN_WORD = 3

#: Сколько фраз-источников помнить на слово. Нужна одна — показать в подсказке, — но первая
#: встреченная может оказаться вычурной, поэтому берём самую короткую из нескольких.
SOURCES_PER_WORD = 4


class KtruSuggestionIndex:
    """Слова справочника, готовые к префиксному поиску и к продолжению по соседству.

    Строится лениво и один раз на процесс: обход всего справочника стоит дороже, чем
    обслуживание тысяч подсказок по готовому индексу. Под замком — gunicorn обслуживает
    запросы в потоках, и два потока, строящие индекс одновременно, сделали бы работу дважды.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = False
        #: отсортированные слова — по ним идёт bisect
        self._words: list[str] = []
        #: слово → (вид, частота, фраза-источник, стоит ли первым в наименовании позиции)
        self._meta: dict[str, tuple[SuggestionKind, int, str, bool]] = {}
        #: слово → слова, встречающиеся сразу после него
        self._next: dict[str, Counter[str]] = {}

    # --- построение -----------------------------------------------------------------------

    def _build(self) -> None:
        kinds: dict[str, SuggestionKind] = {}
        counts: Counter[str] = Counter()
        sources: dict[str, list[str]] = defaultdict(list)
        following: dict[str, Counter[str]] = defaultdict(Counter)
        heads: set[str] = set()

        def absorb(phrase: str, kind: SuggestionKind) -> None:
            if not phrase:
                return
            tokens = [token.casefold() for token in _WORD.findall(phrase)]
            for index, token in enumerate(tokens):
                if len(token) < MIN_WORD or token.isdigit():
                    continue
                counts[token] += 1
                if index == 0 and kind is SuggestionKind.POSITION:
                    heads.add(token)
                # Вид фиксируется по самому весомому источнику: слово из наименования
                # позиции остаётся «позиционным», даже если встретится и в значении.
                if kinds.get(token) is None or _weight(kind) > _weight(kinds[token]):
                    kinds[token] = kind
                    sources[token] = []
                same_weight = _weight(kind) == _weight(kinds[token])
                if same_weight and len(sources[token]) < SOURCES_PER_WORD:
                    sources[token].append(phrase)
                if index + 1 < len(tokens):
                    nxt = tokens[index + 1]
                    if len(nxt) >= MIN_WORD and not nxt.isdigit():
                        following[token][nxt] += 1

        for (name,) in Position.objects.values_list("name").iterator(chunk_size=2000):
            absorb(name, SuggestionKind.POSITION)
        for (name,) in Characteristic.objects.values_list("name").iterator(chunk_size=2000):
            absorb(name, SuggestionKind.CHARACTERISTIC)
        for (text,) in (
            PermittedValue.objects.exclude(text="").values_list("text").iterator(chunk_size=2000)
        ):
            absorb(text, SuggestionKind.VALUE)

        self._meta = {
            word: (
                kinds[word],
                counts[word],
                # Самая короткая из запомненных фраз: она читается как пояснение, а не как
                # отдельная задача на понимание.
                min(sources[word], key=len) if sources[word] else "",
                word in heads,
            )
            for word in counts
        }
        self._words = sorted(self._meta)
        self._next = {word: counter for word, counter in following.items()}
        logger.info("Индекс подсказок КТРУ: %s слов", len(self._words))

    def _ensure(self) -> None:
        if self._ready:
            return
        with self._lock:
            if not self._ready:
                self._build()
                self._ready = True

    def invalidate(self) -> None:
        """Сбросить индекс — после изменения справочника."""
        with self._lock:
            self._ready = False
            self._words = []
            self._meta = {}
            self._next = {}

    # --- поиск ----------------------------------------------------------------------------

    def starting_with(self, prefix: str, limit: int) -> list[Suggestion]:
        """Слова справочника, начинающиеся на ``prefix``.

        Возвращаются **все** совпадения, без обрезки. Обрезать здесь нельзя: список
        отсортирован по алфавиту, а ранжируются подсказки по весу источника и частоте, то есть
        отсечение «первых N» выбросило бы кандидатов до того, как их сравнили. На префиксе
        «по» таких слов 146, и при пределе в 80 самое нужное могло остаться за чертой.

        Позволительно это потому, что весь индекс — 2702 слова: собрать и отранжировать даже
        все из них дешевле, чем один сетевой запрос.
        """
        self._ensure()
        import bisect

        folded = prefix.casefold()
        if not folded:
            return []

        start = bisect.bisect_left(self._words, folded)
        found: list[Suggestion] = []
        # Отсортированный список: все совпадения лежат подряд начиная с найденной позиции.
        for word in self._words[start:]:
            if not word.startswith(folded):
                break
            kind, frequency, source, is_head = self._meta[word]
            found.append(
                Suggestion(
                    word=word, kind=kind, source=source, frequency=frequency, is_head=is_head
                )
            )
        return found

    def following(self, word: str, limit: int) -> list[Suggestion]:
        """Слова, которые в справочнике стоят сразу после ``word``.

        Это и есть «подскажи следующее слово»: после «куртка» справочник говорит «зимняя»,
        «мужская», «из» — не потому, что так устроен русский язык, а потому, что так устроен
        КТРУ, и именно туда пользователя надо вести.
        """
        self._ensure()
        counter = self._next.get(word.casefold())
        if not counter:
            return []
        found: list[Suggestion] = []
        for nxt, frequency in counter.most_common(limit * 4):
            kind, _, source, is_head = self._meta.get(
                nxt, (SuggestionKind.VALUE, 0, "", False)
            )
            found.append(
                Suggestion(
                    word=nxt, kind=kind, source=source, frequency=frequency, is_head=is_head
                )
            )
        return found


def _weight(kind: SuggestionKind) -> int:
    from apps.autofill.domain.suggest import WEIGHTS

    return WEIGHTS[kind]
