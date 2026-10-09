"""КТРУ as the spelling vocabulary.

This is what makes the corrector *task-oriented*. The words worth correcting towards are not
general Russian and not an invented product list — they are the words the КТРУ reference
actually uses, because the whole point of the flow is to land the user on a КТРУ code:

* **characteristic names** — «Длина рукоятки», «Безынерционный», «Материал корпуса»;
* **permitted text values** — «Пластик», «Сварная», «Да»;
* **ОКЕИ unit names** — «Миллиметр», «Килограмм», «Штука»;
* **position names** — «Молоток», «Дрель-шуруповёрт».

Weights reflect what a user types most and what matters most to get right: a misspelled
characteristic name derails the narrowing entirely, so it outranks a position name.

Implements the ``VocabularySource`` port declared by the ``spellcheck`` module; the container
is what connects the two, so neither imports the other's internals.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable

from django.core.cache import cache

from apps.ktru.infrastructure.models import Characteristic, PermittedValue, Position
from apps.spellcheck.domain.correction import tokenize

logger = logging.getLogger(__name__)

CACHE_KEY = "ktru:vocabulary:v1"
BIGRAM_CACHE_KEY = "ktru:bigrams:v1"
CACHE_TTL = 60 * 60 * 24

#: One- and two-character tokens are noise as correction targets; they attract everything.
MIN_TERM_LENGTH = 3

#: Relative importance when two candidates are an equal edit away.
WEIGHT_CHARACTERISTIC = 6
WEIGHT_VALUE = 5
WEIGHT_UNIT = 4
WEIGHT_POSITION = 2


class KtruVocabulary:
    """``(word, frequency)`` over the КТРУ reference."""

    def terms(self) -> Iterable[tuple[str, int]]:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached

        counter: Counter[str] = Counter()

        def add(text: str, weight: int) -> None:
            for token in tokenize(text or ""):
                counter[token] += weight

        # `values_list(...).iterator()` streams and fetches one column: the reference is large
        # enough that materialising it would be the peak memory of the whole process.
        for (name,) in Characteristic.objects.values_list("name").iterator(chunk_size=2000):
            add(name, WEIGHT_CHARACTERISTIC)

        for text, unit_name in PermittedValue.objects.values_list(
            "text", "unit_name"
        ).iterator(chunk_size=2000):
            add(text, WEIGHT_VALUE)
            add(unit_name, WEIGHT_UNIT)

        for (name,) in Position.objects.values_list("name").iterator(chunk_size=2000):
            add(name, WEIGHT_POSITION)

        terms = [
            (word, count)
            for word, count in counter.items()
            if len(word) >= MIN_TERM_LENGTH and not word.isdigit()
        ]
        logger.info("КТРУ vocabulary: %s terms", len(terms))
        cache.set(CACHE_KEY, terms, CACHE_TTL)
        return terms

    def bigrams(self) -> Iterable[tuple[str, int]]:
        """Adjacent word pairs, for choosing between equally-close candidates by context."""
        cached = cache.get(BIGRAM_CACHE_KEY)
        if cached is not None:
            return cached

        counter: Counter[str] = Counter()

        def add(text: str, weight: int) -> None:
            tokens = tokenize(text or "")
            for left, right in zip(tokens, tokens[1:], strict=False):
                counter[f"{left} {right}"] += weight

        for (name,) in Characteristic.objects.values_list("name").iterator(chunk_size=2000):
            add(name, WEIGHT_CHARACTERISTIC)
        for (text,) in PermittedValue.objects.values_list("text").iterator(chunk_size=2000):
            add(text, WEIGHT_VALUE)
        for (name,) in Position.objects.values_list("name").iterator(chunk_size=2000):
            add(name, WEIGHT_POSITION)

        pairs = list(counter.items())
        logger.info("КТРУ bigrams: %s pairs", len(pairs))
        cache.set(BIGRAM_CACHE_KEY, pairs, CACHE_TTL)
        return pairs

    @staticmethod
    def invalidate() -> None:
        cache.delete(CACHE_KEY)
        cache.delete(BIGRAM_CACHE_KEY)
