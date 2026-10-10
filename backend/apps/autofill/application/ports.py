"""Ports the analyser depends on."""

from __future__ import annotations

from typing import Protocol

from apps.autofill.domain.entities import PositionCandidate
from apps.autofill.domain.gaps import ReferenceGap
from apps.autofill.domain.suggest import Suggestion
from apps.ktru.domain.entities import Characteristic
from apps.spellcheck.domain.correction import Correction


class KtruCatalog(Protocol):
    """Read access to the КТРУ reference.

    The analyser never touches the ORM: the whole narrowing algorithm is pure logic over
    domain objects, which is what lets it be tested without a database.
    """

    def find_positions(self, text: str, *, limit: int) -> list[PositionCandidate]:
        """Positions whose name resembles the description, best first."""
        ...

    def characteristics_for(self, codes: list[str]) -> dict[str, list[Characteristic]]:
        """The characteristics of each given position, with their permitted values."""
        ...


class QueryCorrector(Protocol):
    def execute(self, raw_query: str) -> Correction: ...


class UnitNormalizer(Protocol):
    def __call__(self, written: str) -> str | None:
        """The ОКЕИ name for what the user wrote, or ``None`` when it is not a known unit."""
        ...


class ReferenceVocabulary(Protocol):
    """«Знает ли справочник это слово вообще?»

    Отделяет «у этой позиции нет такой характеристики» от «такого понятия в КТРУ нет совсем» —
    два наблюдения, которые ведущему справочник нужно разбирать по-разному.
    """

    def knows(self, word: str) -> bool: ...


class GapRecorder(Protocol):
    """Куда складываются наблюдения о нехватке в справочнике.

    Отдельный порт, а не прямая запись в модель: сбор — **необязательная** часть работы
    сервиса, и выключить его должно быть можно, не трогая разбор. Реализация в
    ``infrastructure`` копит счётчики; заглушка в тестах просто собирает список.
    """

    def record(self, gaps: list[ReferenceGap]) -> None: ...


class SuggestionIndex(Protocol):
    """Словарь справочника, готовый отвечать по мере набора."""

    def starting_with(self, prefix: str, limit: int) -> list[Suggestion]:
        """Слова, начинающиеся на ``prefix``."""
        ...

    def following(self, word: str, limit: int) -> list[Suggestion]:
        """Слова, стоящие в справочнике сразу после ``word``."""
        ...
