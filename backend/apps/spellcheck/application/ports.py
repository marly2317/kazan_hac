"""Ports the correction pipeline depends on. Implementations live in ``infrastructure``."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class QueryRewriter(Protocol):
    """One stage of the pipeline.

    Returns the rewritten query, or ``None`` when this stage has nothing to say. ``None`` rather
    than "the same string back" so the use case can tell "I looked and it was fine" apart from
    "I could not help", and attribute the correction to the stage that actually made it.

    A stage must never raise for a reason the user cannot act on: a dead language model is a
    reason to skip the stage, not to fail the search.
    """

    def rewrite(self, query: str) -> str | None: ...


class VocabularySource(Protocol):
    """The words worth correcting *towards*.

    Supplied by whatever module owns the searchable text — for this application, the catalogue.
    Correcting against a general Russian dictionary is the classic mistake: it rewrites a real
    product name into a more common ordinary word and the search then returns nothing.
    """

    def terms(self) -> Iterable[tuple[str, int]]:
        """``(word, frequency)`` pairs. Frequency breaks ties between equally close candidates."""
        ...

    def bigrams(self) -> Iterable[tuple[str, int]]:
        """``("слово1 слово2", frequency)`` pairs — adjacent words as they occur in the source.

        Frequency alone cannot choose between two candidates an equal edit away: «кожы» is one
        edit from both «кожа» and «кожи», and the more common word wins regardless of what the
        sentence needs. A neighbouring word decides it — «из кожи» occurs in the reference and
        «из кожа» does not.
        """
        ...


class CorrectionCache(Protocol):
    """Memoizes the expensive stages. The same misspelling recurs constantly across users."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...
