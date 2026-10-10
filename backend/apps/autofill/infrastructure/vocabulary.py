"""Словарь КТРУ в виде вопроса «знает ли справочник это слово»."""

from __future__ import annotations

import threading

from apps.autofill.application.ports import ReferenceVocabulary
from apps.spellcheck.application.ports import VocabularySource


class CachedReferenceVocabulary(ReferenceVocabulary):
    """Множество известных справочнику слов, построенное один раз на процесс.

    Тот же словарь, по которому исправляются опечатки, — но нужен другой вопрос: не «на какое
    слово похоже», а «есть ли такое вообще». Множество строится лениво и под замком: gunicorn
    обслуживает запросы в потоках, и два потока, строящие его одновременно, сделали бы работу
    дважды.
    """

    def __init__(self, source: VocabularySource) -> None:
        self._source = source
        self._words: frozenset[str] | None = None
        self._lock = threading.Lock()

    def knows(self, word: str) -> bool:
        return word.casefold() in self._known()

    def _known(self) -> frozenset[str]:
        if self._words is None:
            with self._lock:
                if self._words is None:
                    self._words = frozenset(w for w, _ in self._source.terms())
        return self._words

    def invalidate(self) -> None:
        with self._lock:
            self._words = None
