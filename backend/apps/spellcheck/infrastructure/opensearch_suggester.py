"""Словарное исправление через `term suggester` OpenSearch.

Замена `symspell_index.py`, и замена **частичная по смыслу**: движку передана генерация
кандидатов, а правила выбора остались здесь. Разделение не произвольное — оно по линии «поиск
против предметной области».

## Что ушло движку

Построение индекса опечаток и поиск по нему. Это и есть вся стоимость SymSpell: он
предвычисляет удаления по всему словарю и держит результат в памяти **процесса**, то есть
gunicorn с тремя воркерами строит его трижды и хранит в трёх копиях, а первый пользователь
после перезапуска ждёт постройки. `term suggester` делает то же по индексу, который уже
построен и один на стенд.

Заодно два наших правила оказались встроенными в движок, и это приятно:

* **первая буква обязана сохраниться** — `prefix_length: 1`. Правило, без которого «крестовая»
  превращалась в «ростовую»: опечатка почти всегда внутри слова, а кандидат с другой первой
  буквой — это не это слово с ошибкой, а другое слово;
* **не исправлять то, что в словаре есть** — `suggest_mode: missing`. Слово справочника не
  опечатка, сколько бы похожих на него ни нашлось.

## Что осталось здесь, и почему не передано

**Выбор между равноудалёнными кандидатами по соседнему слову.** «кожы» отстоит на одну правку
и от «кожа», и от «кожи»; частота выбирает более частое, а нужное выбирает сосед — «из кожи» в
справочнике есть, «из кожа» нет. У движка для этого есть `phrase suggester`, но он строит
вероятностную модель по шинглам, а у нас правило точное и проверяемое: есть такая пара слов в
справочнике или нет. Менять точное правило на модель, которая угадывает то же самое, — размен
не в нашу пользу.

**Какие слова вообще можно трогать** (`is_correctable`): короче трёх букв, служебные, с цифрой
внутри — `PH2` не опечатка русского слова. Это правило про предметную область, и оно одно для
всех стадий.

**Регистр и подстановка** — `domain/substitution.py`, общее с SymSpell.
"""

from __future__ import annotations

import logging
import threading

from apps.spellcheck.application.ports import VocabularySource
from apps.spellcheck.domain.correction import is_correctable, tokenize
from apps.spellcheck.domain.substitution import replace_token

logger = logging.getLogger(__name__)

#: Правок, на которые движок отходит от набранного слова.
#:
#: Два — как и у SymSpell: столько покрывает подавляющее большинство настоящих опечаток, а
#: три начинают превращать разные слова друг в друга.
MAX_EDITS = 2

#: Длина, с которой разрешены две правки.
#:
#: Ниже неё две правки — это большая часть слова, и исправление становится произвольным
#: («ток» → «сок» → «бок»). Семь, а не пять: на «крестовая» → «ростовая» видно, что
#: девятибуквенное слово, которого в справочнике просто нет, на расстоянии двух правок
#: доходит до другого слова, и получается не исправленный запрос, а другой запрос.
SHORT_TOKEN_LENGTH = 7

#: Сколько кандидатов просить на слово. Больше одного — потому что выбирает сосед, а не
#: движок: при одном кандидате выбирать было бы не из чего.
CANDIDATES = 8


class OpenSearchRewriter:
    """Исправление слов по словарю справочника, лежащему в индексе движка."""

    def __init__(self, vocabulary: VocabularySource, index: str | None = None) -> None:
        # Словарь нужен **только** для пар соседних слов: кандидатов даёт движок. Отдельного
        # индекса биграмм в движке нет намеренно — пара слов это точный факт о справочнике, и
        # держать его множеством в памяти дешевле, чем спрашивать по сети на каждое слово.
        self._vocabulary = vocabulary
        self._index = index
        self._bigrams: dict[str, int] | None = None
        self._lock = threading.Lock()

    @property
    def index(self) -> str:
        from apps.ktru.infrastructure.search.indexes import TERMS

        return self._index or TERMS.name

    def _bigram_counts(self) -> dict[str, int]:
        if self._bigrams is None:
            with self._lock:
                if self._bigrams is None:
                    source = getattr(self._vocabulary, "bigrams", None)
                    self._bigrams = dict(source()) if callable(source) else {}
        return self._bigrams

    def invalidate(self) -> None:
        with self._lock:
            self._bigrams = None

    def rewrite(self, query: str) -> str | None:
        """Исправленный запрос, либо ``None``, когда исправлять нечего.

        ``None``, а не та же строка: сценарий различает «посмотрел, всё в порядке» и «не смог
        помочь», и приписывает исправление той стадии, которая его сделала.
        """
        from apps.ktru.infrastructure.search.client import client

        connection = client()
        if connection is None:
            return None

        tokens = tokenize(query)
        correctable = [token for token in tokens if is_correctable(token)]
        if not correctable:
            return None

        try:
            candidates = self._ask(connection, correctable)
        except Exception as error:  # noqa: BLE001
            # Стадия обязана не падать по причине, с которой пользователь ничего не сделает:
            # недоступный движок — повод пропустить стадию, а не отказать в поиске.
            logger.warning("Подсказчик опечаток недоступен (%s)", error)
            return None

        replacements: dict[str, str] = {}
        for position, token in enumerate(tokens):
            if token in replacements or token not in candidates:
                continue
            options = candidates[token]
            if not options:
                continue

            # Соседи — **уже исправленные**: фраза с двумя опечатками подряд иначе
            # сопоставлялась бы с опечаткой рядом.
            left = tokens[position - 1] if position else ""
            right = tokens[position + 1] if position + 1 < len(tokens) else ""
            left = replacements.get(left, left)
            right = replacements.get(right, right)

            choice = self._choose(options, left, right)
            if choice != token:
                replacements[token] = choice

        if not replacements:
            return None

        corrected = query
        for token, replacement in replacements.items():
            corrected = replace_token(corrected, token, replacement)
        return corrected

    def _ask(self, connection, tokens: list[str]) -> dict[str, list[str]]:
        """Кандидаты на каждое слово — одним запросом.

        Подсказка на слово, а не одна на весь текст: `term suggester` разбирает присланную
        строку своим анализатором и возвращает варианты по токенам, но тогда соответствие
        «слово → его варианты» пришлось бы восстанавливать по смещениям. Отдельная именованная
        подсказка на слово отдаёт это соответствие готовым, а запрос всё равно один.
        """
        body = {
            "size": 0,
            "suggest": {
                f"t{position}": {
                    "text": token,
                    "term": {
                        "field": "word",
                        # Только то, чего в словаре нет: слово справочника не опечатка.
                        "suggest_mode": "missing",
                        "max_edits": MAX_EDITS if len(token) >= SHORT_TOKEN_LENGTH else 1,
                        # Первая буква обязана сохраниться — см. верхний комментарий.
                        "prefix_length": 1,
                        "min_word_length": 3,
                        "size": CANDIDATES,
                        # По частоте: она разрешает ничьи, когда сосед не говорит ничего.
                        # Порядок важен — `_choose` при равном вкладе соседа берёт первого.
                        "sort": "frequency",
                        "string_distance": "levenshtein",
                    },
                }
                for position, token in enumerate(tokens)
            },
        }

        response = connection.search(index=self.index, body=body)
        suggest = response.get("suggest", {})

        found: dict[str, list[str]] = {}
        for position, token in enumerate(tokens):
            groups = suggest.get(f"t{position}", [])
            options = [
                option["text"]
                for group in groups
                for option in group.get("options", [])
                # Первая буква — ещё раз, уже по ответу. `prefix_length` движка работает по
                # токенам **анализатора**, а словарь проиндексирован анализатором `ru_word`
                # (слово целиком); совпадение двух представлений лучше проверить, чем
                # предположить.
                if option["text"][:1] == token[:1]
            ]
            if options:
                found[token] = options
        return found

    def _choose(self, candidates: list[str], left: str, right: str) -> str:
        """Выбрать кандидата, которого поддерживают соседние слова.

        Частота сама по себе выбирает более частое слово — и это неверно именно тогда, когда
        запрос фраза: «кожы» на одну правку и от «кожа», и от «кожи», и решает только сосед.
        Кандидаты приходят упорядоченными по частоте, поэтому нулевой вклад соседа всюду
        возвращает поведение к частотному.
        """
        bigrams = self._bigram_counts()
        if not bigrams:
            return candidates[0]

        def score(candidate: str) -> int:
            total = 0
            if left:
                total += bigrams.get(f"{left} {candidate}", 0)
            if right:
                total += bigrams.get(f"{candidate} {right}", 0)
            return total

        # `max` оставляет первого из равных, а список упорядочен по частоте — ничья
        # разрешается так же, как разрешалась до появления биграмм.
        return max(candidates, key=score)
