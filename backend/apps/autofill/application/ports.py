"""Ports the analyser depends on."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from apps.autofill.domain.entities import ExtractedCharacteristic, PositionCandidate
from apps.autofill.domain.gaps import ReferenceGap
from apps.autofill.domain.query import Lexicon, SplitQuery
from apps.autofill.domain.suggest import Suggestion
from apps.ktru.domain.entities import Characteristic
from apps.spellcheck.domain.correction import Correction

__all__ = [
    "CatalogNarrowing",
    "GapRecorder",
    "KtruCatalog",
    "Lexicon",
    "Narrowing",
    "QueryCorrector",
    "ReferenceVocabulary",
    "SuggestionIndex",
    "UnitNormalizer",
]


class KtruCatalog(Protocol):
    """Read access to the КТРУ reference.

    The analyser never touches the ORM: the whole narrowing algorithm is pure logic over
    domain objects, which is what lets it be tested without a database.
    """

    def find_positions(self, query: SplitQuery, *, limit: int) -> list[PositionCandidate]:
        """Positions whose name resembles the description, best first.

        Takes the **split** description, not the raw text: the part that names the product is
        what the position names are searched by, and the part that describes it has no business
        in that comparison. See :func:`apps.autofill.domain.query.split`.
        """
        ...

    def characteristics_for(self, codes: list[str]) -> dict[str, list[Characteristic]]:
        """The characteristics of each given position, with their permitted values."""
        ...


@dataclass(frozen=True)
class Narrowing:
    """Сужение, выполненное поисковым движком.

    Отдельный тип, а не кортеж, потому что `steps` здесь — не отчёт о проделанной работе, а
    **измерение**: сколько позиций всего отвечает условию на каждом шаге. Длина списка
    кандидатов этого не даёт — она ограничена размером страницы, а не числом совпадений.
    """

    #: Выжившие кандидаты, у каждого заполнен `support`.
    candidates: list[PositionCandidate] = field(default_factory=list)
    #: ``(имя характеристики, сколько было, сколько стало)`` по шагам, в порядке применения.
    steps: list[tuple[str, int, int]] = field(default_factory=list)
    #: Сколько позиций отвечает всем условиям — не обрезано страницей.
    total: int = 0


class CatalogNarrowing(Protocol):
    """Сужение по характеристикам средствами движка.

    Отдельный порт, а не метод :class:`KtruCatalog`, потому что это **возможность**, а не
    обязанность: реализация на Postgres её не предоставляет, и сужение тогда делает сам
    use case перебором пула. Разница не в результате, а в охвате — условие в запросе
    применяется ко всему индексу, а перебор только к отобранному пулу, и позиция, чьё
    наименование не попало в пул, никакими характеристиками в нём не восстановится.
    """

    def narrow(
        self,
        query: SplitQuery,
        items: list[ExtractedCharacteristic],
        *,
        within: list[str],
        limit: int,
    ) -> Narrowing:
        """Позиции, которым не противоречит ни одна из распознанных характеристик.

        ``within`` — коды позиций с разрешённым наименованием. Сужение идёт внутри них:
        наименование определяется до характеристик, и то, что описание не называет, уже
        отвергнуто.
        """
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
