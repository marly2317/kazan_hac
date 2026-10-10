"""Пробелы в справочнике: чего, судя по запросам пользователей, в КТРУ не хватает.

Эта часть работает **не на пользователя**. Пользователю КТРУ — закон: сервис обязан привести
его к существующему коду и не вправе обещать характеристику, которой в справочнике нет.
Но сами запросы — ценные данные о том, где справочник отстал от того, как люди описывают
товары, и эти наблюдения адресованы тем, кто справочник ведёт.

Логика простая и намеренно консервативная: **после того как товар уверенно определён**, каждое
слово описания, которое разбор не смог объяснить, — кандидат в пробелы. «Уверенно определён»
здесь обязательно: пока мы не знаем, о каком товаре речь, необъяснённое слово говорит о
неудачном поиске, а не о дырке в справочнике, и такие наблюдения только зашумят отчёт.

Чистые функции над доменными объектами: ни ORM, ни Django. Правило, по которому слово
считается пробелом, — часть предметной области, а не деталь хранения.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from apps.autofill.application.extraction import stem
from apps.autofill.domain.entities import Analysis
from apps.autofill.domain.enums import IssueKind, MatchState
from apps.autofill.domain.matching import words


class GapKind(StrEnum):
    """Чего именно не хватает. Виды различаются тем, что с ними делать дальше."""

    #: Слово есть в справочнике, но позиция, которую мы определили, такой характеристики не
    #: объявляет. Самый сильный сигнал: понятие КТРУ уже знает, значит вопрос только в том,
    #: чтобы добавить характеристику этой позиции, а не придумывать её с нуля.
    MISSING_FOR_POSITION = "missing_for_position"
    #: Слова нет в справочнике нигде. Возможно, новая характеристика или новое значение;
    #: возможно, просто слово, которое к характеристикам отношения не имеет. Поэтому —
    #: отдельный вид: такие наблюдения требуют более придирчивого разбора.
    UNKNOWN_TERM = "unknown_term"
    #: Единица измерения, которой нет в ОКЕИ-подмножестве справочника.
    UNKNOWN_UNIT = "unknown_unit"


GAP_KIND_CHOICES = [(kind.value, kind.value) for kind in GapKind]


@dataclass(frozen=True)
class ReferenceGap:
    """Одно наблюдение: такого-то в справочнике не хватило вот для этой позиции."""

    term: str
    kind: GapKind
    #: Позиция, которую разбор определил, — контекст наблюдения. Без неё «колесиках» не значит
    #: ничего, с ней это «у „Стеллаж офисный“ нет характеристики про колёса».
    position_code: str
    position_name: str
    okpd2_code: str
    #: Описание целиком — чтобы человек, разбирающий отчёт, видел исходную формулировку.
    description: str


def find_gaps(analysis: Analysis, is_known_term: Callable[[str], bool]) -> list[ReferenceGap]:
    """Слова описания, которые разбор не смог объяснить определённой позицией.

    ``is_known_term`` отвечает на вопрос «есть ли это слово в словаре КТРУ вообще» — именно он
    и делит наблюдения на «характеристики не хватает вот здесь» и «такого слова справочник не
    знает совсем».
    """
    if analysis.state not in (MatchState.RESOLVED, MatchState.AMBIGUOUS):
        # Товар не определён: необъяснённое слово — симптом неудачного поиска, а не пробела.
        return []
    if not analysis.candidates:
        return []

    leader = analysis.candidates[0]
    explained = _explained_stems(analysis, leader.name)

    gaps: list[ReferenceGap] = []
    seen: set[str] = set()

    # Единицы, которых нет в ОКЕИ, разбор уже нашёл и объяснил пользователю. Здесь они нужны
    # второй раз и для другого читателя: пользователю сказали «перепишите», а ведущему
    # справочник — «эту единицу у вас спрашивают».
    for issue in analysis.issues:
        if issue.kind is not IssueKind.UNKNOWN_UNIT:
            continue
        unit = issue.raw.split()[-1] if issue.raw else ""
        if not unit or stem(unit) in seen:
            continue
        seen.add(stem(unit))
        gaps.append(
            ReferenceGap(
                term=unit,
                kind=GapKind.UNKNOWN_UNIT,
                position_code=leader.code,
                position_name=leader.name,
                okpd2_code=leader.okpd2_code,
                description=analysis.corrected,
            )
        )


    # Общий проход — после единиц: «попугаев» и так попало бы сюда как неизвестное слово, но
    # «неизвестная единица измерения» — более точный диагноз, и побеждать должен он.
    for word in words(analysis.corrected):
        key = stem(word)
        if key in explained or key in seen:
            continue
        seen.add(key)
        gaps.append(
            ReferenceGap(
                term=word,
                kind=(
                    GapKind.MISSING_FOR_POSITION
                    if is_known_term(word)
                    else GapKind.UNKNOWN_TERM
                ),
                position_code=leader.code,
                position_name=leader.name,
                okpd2_code=leader.okpd2_code,
                description=analysis.corrected,
            )
        )

    return gaps


def _explained_stems(analysis: Analysis, leader_name: str) -> set[str]:
    """Всё, что разбор уже учёл, в виде основ слов.

    По основам, а не по точным словам: описание говорит «шкафы», позиция называется «Шкаф»,
    и считать второе необъяснённым — значит каждый раз сообщать о пробеле там, где его нет.
    """
    explained: set[str] = {stem(word) for word in words(leader_name)}

    for item in analysis.characteristics:
        for source in (item.name, item.raw, item.value, item.unit, item.raw_unit):
            explained.update(stem(word) for word in words(source))

    # Исправленные слова здесь намеренно **не** учитываются. Соблазн был: «кострукция» ведь
    # уже объяснена — мы знаем, что это «конструкция». Но в разборе участвует именно
    # исправленный текст, и он проходит общий проход сам; а вот исключение по исходному
    # написанию заодно глушило и само исправление, потому что основа у них общая:
    # «колесиках» → «колесиков» давало одну основу «колес», и настоящий пробел исчезал.
    return explained
