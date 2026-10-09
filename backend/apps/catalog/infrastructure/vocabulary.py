"""The catalogue as a spelling vocabulary.

This is the adapter that lets the ``spellcheck`` module correct towards words that exist in
*this* catalogue. It implements a port that module declares, and the container is what connects
them — neither module imports the other's internals.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable

from django.core.cache import cache

from apps.catalog.infrastructure.models import Product as ProductModel
from apps.spellcheck.domain.correction import tokenize

logger = logging.getLogger(__name__)

CACHE_KEY = "catalog:vocabulary:v1"
CACHE_TTL = 60 * 60

#: One- and two-character tokens are units and noise (``шт``, ``мм``, ``а4``); as correction
#: targets they attract everything.
MIN_TERM_LENGTH = 3


class CatalogVocabulary:
    """``(word, frequency)`` over product names, categories and descriptions.

    Frequency is a real count rather than a flat 1: it is what makes SymSpell prefer the word
    that appears across fifty products over the one that appears in a single description, when
    both are an equal edit away from what the user typed.
    """

    def terms(self) -> Iterable[tuple[str, int]]:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached

        counter: Counter[str] = Counter()
        # `iterator()` streams: the whole catalogue does not have to fit in memory at once, and
        # only the three text columns are fetched.
        rows = ProductModel.objects.values_list("name", "category", "description").iterator(
            chunk_size=500
        )
        for name, category, description in rows:
            # The name is the field people actually search, so its words are weighted up.
            for token in tokenize(name):
                counter[token] += 5
            for token in tokenize(category):
                counter[token] += 3
            for token in tokenize(description):
                counter[token] += 1

        terms = [
            (word, count)
            for word, count in counter.items()
            if len(word) >= MIN_TERM_LENGTH and not word.isdigit()
        ]
        logger.info("Catalogue vocabulary: %s terms", len(terms))
        cache.set(CACHE_KEY, terms, CACHE_TTL)
        return terms

    def bigrams(self) -> Iterable[tuple[str, int]]:
        """The demo catalogue is too small for word-pair statistics to mean anything."""
        return []

    @staticmethod
    def invalidate() -> None:
        cache.delete(CACHE_KEY)
