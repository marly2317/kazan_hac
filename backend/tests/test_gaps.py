"""Наблюдения о нехватке в справочнике. Чистая логика — ни базы, ни Django.

Главный риск этой возможности — не пропустить пробел, а **зашуметь**: отчёт, в котором на
каждое наблюдение приходится десяток обрывков слов и очевидных совпадений, читать никто не
станет, и возможность окажется бесполезной ровно в тот момент, когда понадобится. Поэтому
большинство тестов здесь — о том, что в отчёт **не** попадает.
"""

from __future__ import annotations

from apps.autofill.domain.entities import (
    Analysis,
    ExtractedCharacteristic,
    Issue,
    PositionCandidate,
)
from apps.autofill.domain.enums import IssueKind, MatchState
from apps.autofill.domain.gaps import GapKind, find_gaps

#: Словарь справочника для теста: «колесиков» справочник знает, «вайфай» — нет.
KNOWN = {"колесиков", "черный", "конструкция", "сварная"}


def knows(word: str) -> bool:
    return word.casefold() in KNOWN


def analysis(
    description: str,
    *,
    original: str | None = None,
    name: str = "Стеллаж офисный",
    characteristics: list[ExtractedCharacteristic] | None = None,
    issues: list[Issue] | None = None,
    overlap: float = 1.0,
) -> Analysis:
    return Analysis(
        original=original or description,
        corrected=description,
        characteristics=characteristics or [],
        issues=issues or [],
        steps=[],
        candidates=[
            PositionCandidate(
                code="31.01.10.000-00000007",
                name=name,
                okpd2_code="31.01.10.000",
                score=3.0,
                name_overlap=overlap,
            )
        ],
        next_steps=[],
        total_candidates=2,
    )


# --- что попадает в отчёт -------------------------------------------------------------------


def test_a_word_the_reference_knows_elsewhere_is_a_gap_for_this_position() -> None:
    # Самый ценный сигнал: понятие в КТРУ есть, просто у этой позиции такой характеристики
    # нет. Значит вопрос — добавить характеристику, а не придумать её с нуля.
    (gap,) = find_gaps(analysis("стеллаж офисный колесиков"), knows)

    assert gap.term == "колесиков"
    assert gap.kind is GapKind.MISSING_FOR_POSITION
    assert gap.position_code == "31.01.10.000-00000007"
    assert gap.okpd2_code == "31.01.10.000"


def test_a_word_the_reference_does_not_know_is_a_weaker_kind() -> None:
    (gap,) = find_gaps(analysis("стеллаж офисный вайфай"), knows)

    assert gap.kind is GapKind.UNKNOWN_TERM


def test_an_unknown_unit_is_recorded_for_the_reference_owner_too() -> None:
    # Пользователю про неё уже сказали «перепишите». Ведущему справочник нужно другое: эту
    # единицу у вас спрашивают.
    issue = Issue(
        kind=IssueKind.UNKNOWN_UNIT,
        characteristic="",
        raw="350 попугаев",
        message="",
        suggestions=[],
    )
    gaps = find_gaps(analysis("стеллаж офисный 350 попугаев", issues=[issue]), knows)

    assert GapKind.UNKNOWN_UNIT in {gap.kind for gap in gaps}
    assert "попугаев" in {gap.term for gap in gaps}


# --- чего в отчёте быть не должно -------------------------------------------------------------


def test_nothing_is_recorded_while_the_product_is_unknown() -> None:
    # Пока неизвестно, о каком товаре речь, необъяснённое слово — симптом неудачного поиска,
    # а не дырки в справочнике.
    empty = Analysis(
        original="отвертка крестовая",
        corrected="отвертка крестовая",
        characteristics=[],
        issues=[],
        steps=[],
        candidates=[
            PositionCandidate(code="A", name="Кровать детская", okpd2_code="31", score=0.5)
        ],
        next_steps=[],
        total_candidates=1,
    )

    assert empty.state is MatchState.NOT_FOUND
    assert find_gaps(empty, knows) == []


def test_words_of_the_position_name_are_not_gaps() -> None:
    # «стеллаж» и «офисный» — это и есть найденная позиция.
    assert find_gaps(analysis("стеллаж офисный"), knows) == []


def test_an_inflected_form_of_the_name_is_not_a_gap() -> None:
    # Описание говорит «стеллажи», позиция называется «Стеллаж». Сообщать о пробеле здесь —
    # значит сообщать о нём на каждом втором запросе.
    assert find_gaps(analysis("стеллажи офисные"), knows) == []


def test_an_extracted_characteristic_is_not_a_gap() -> None:
    # Её разбор как раз объяснил — тем, что нашёл.
    extracted = [
        ExtractedCharacteristic(name="Вид материала каркаса", raw="Металл", value="Металл")
    ]
    assert find_gaps(analysis("стеллаж офисный металл", characteristics=extracted), knows) == []


def test_the_misspelling_is_never_reported_only_its_correction() -> None:
    # В разборе участвует исправленный текст, и в отчёт должна попадать «конструкция» —
    # слово, которое справочник знает и которого у этой позиции нет. Сообщать о «кострукция»
    # бессмысленно: такого слова не существует, добавлять в КТРУ нечего.
    issue = Issue(
        kind=IssueKind.TYPO,
        characteristic="",
        raw="кострукция",
        message="",
        suggestions=["конструкция"],
    )
    gaps = find_gaps(
        analysis(
            "стеллаж офисный конструкция",
            original="стеллаж офисный кострукция",
            issues=[issue],
        ),
        knows,
    )

    assert {gap.term for gap in gaps} == {"конструкция"}


def test_a_normalised_word_is_still_reported() -> None:
    # «колесиках» → «колесиков» — то же слово в форме справочника. Исключать его по исходному
    # написанию нельзя: основа у обоих одна, и вместе с написанием глохнет сам пробел,
    # ради которого всё и затевалось.
    issue = Issue(
        kind=IssueKind.NORMALIZED,
        characteristic="",
        raw="колесиках",
        message="",
        suggestions=["колесиков"],
    )
    gaps = find_gaps(
        analysis(
            "стеллаж офисный колесиков",
            original="стеллаж офисный колесиках",
            issues=[issue],
        ),
        knows,
    )

    assert {gap.term for gap in gaps} == {"колесиков"}


def test_short_words_and_numbers_are_not_gaps() -> None:
    # Предлоги и значения характеристиками не являются.
    assert find_gaps(analysis("стеллаж офисный из 48 для"), knows) == []


def test_the_same_word_is_reported_once_per_analysis() -> None:
    gaps = find_gaps(analysis("стеллаж офисный вайфай вайфай"), knows)

    assert len(gaps) == 1
