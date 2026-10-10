"""The narrowing pipeline, driven entirely by in-memory doubles — no database, no HTTP.

This is what the port design buys: the algorithm that decides a КТРУ code is pure logic over
domain objects, so its edge cases can be pinned down without a fixture.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.autofill.application.extraction import find_numbers, find_texts
from apps.autofill.application.use_cases.analyze_description import (
    AnalyzeDescription,
    AnalyzeDescriptionInput,
)
from apps.autofill.domain.entities import PositionCandidate
from apps.autofill.domain.enums import IssueKind, MatchState
from apps.autofill.domain.query import SplitQuery
from apps.ktru.domain.entities import Characteristic, PermittedValue, Unit
from apps.ktru.domain.enums import CharacteristicType, ValueKind
from apps.spellcheck.domain.correction import Correction
from apps.spellcheck.domain.enums import CorrectionSource

MM = Unit(code="003", name="Миллиметр")
KG = Unit(code="166", name="Килограмм")

UNITS = {
    "мм": "Миллиметр",
    "м": "Метр",
    "kg": "Килограмм",
    "кг": "Килограмм",
    "г": "Грамм",
}


def normalize_unit(written: str) -> str | None:
    return UNITS.get(written.strip().casefold())


def text_characteristic(name: str, *options: str, required: bool = False) -> Characteristic:
    return Characteristic(
        code=name,
        name=name,
        type=CharacteristicType.QUALITATIVE,
        is_required=required,
        values=[PermittedValue(kind=ValueKind.TEXT, text=option) for option in options],
    )


def range_characteristic(
    name: str, low: str, high: str, unit: Unit = MM, *, required: bool = False
) -> Characteristic:
    return Characteristic(
        code=name,
        name=name,
        type=CharacteristicType.QUANTITATIVE,
        is_required=required,
        values=[
            PermittedValue(
                kind=ValueKind.RANGE,
                minimum=Decimal(low),
                maximum=Decimal(high),
                unit=unit,
            )
        ],
    )


class FakeCatalog:
    """A handful of positions, scored by how well the description matches the name."""

    def __init__(self, positions: dict[str, tuple[str, float, list[Characteristic]]]) -> None:
        self._positions = positions

    def find_positions(self, query: SplitQuery, *, limit: int) -> list[PositionCandidate]:
        return sorted(
            (
                PositionCandidate(code=code, name=name, okpd2_code="00", score=score)
                for code, (name, score, _) in self._positions.items()
            ),
            key=lambda candidate: -candidate.score,
        )[:limit]

    def characteristics_for(self, codes: list[str]) -> dict[str, list[Characteristic]]:
        return {code: self._positions[code][2] for code in codes if code in self._positions}


class FakeCorrector:
    def __init__(self, corrected: str | None = None) -> None:
        self._corrected = corrected

    def execute(self, raw_query: str) -> Correction:
        if self._corrected is None:
            return Correction.unchanged(raw_query)
        return Correction(
            original=raw_query, corrected=self._corrected, source=CorrectionSource.DICTIONARY
        )


def analyser(catalog: FakeCatalog, corrector: FakeCorrector | None = None) -> AnalyzeDescription:
    return AnalyzeDescription(catalog, corrector or FakeCorrector(), normalize_unit)


# --- extraction -------------------------------------------------------------------------


def test_numbers_are_found_with_their_units_normalised() -> None:
    found = find_numbers("молоток 350 мм и 1,5 кг", normalize_unit)

    assert [(str(m.number), m.unit) for m in found] == [("350", "Миллиметр"), ("1.5", "Килограмм")]


def test_a_comma_decimal_is_read_as_a_decimal() -> None:
    # "1,5" is how a number is written in Russian; reading it as 1 would be silently wrong.
    assert find_numbers("вес 1,5 кг", normalize_unit)[0].number == Decimal("1.5")


def test_text_values_match_only_on_word_boundaries() -> None:
    characteristics = [text_characteristic("Безынерционный", "Да", "Нет")]

    # "Да" must not match inside "Дальность".
    assert find_texts("дальность броска", characteristics) == []
    assert find_texts("безынерционный да", characteristics)[0].value == "Да"


def test_the_longest_permitted_value_wins() -> None:
    characteristics = [text_characteristic("Вид", "Мужской", "Мужской костюм")]

    found = find_texts("мужской костюм", characteristics)

    assert [m.value for m in found] == ["Мужской костюм"]


# --- narrowing --------------------------------------------------------------------------


def test_a_single_surviving_position_is_reported_as_resolved() -> None:
    catalog = FakeCatalog({"A": ("Молоток", 3.0, [text_characteristic("Тип", "Слесарный")])})

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="молоток слесарный"))

    assert result.state is MatchState.RESOLVED
    assert result.resolved is not None
    assert result.resolved.code == "A"


def test_a_contradicting_position_is_eliminated() -> None:
    catalog = FakeCatalog(
        {
            "A": ("Куртка", 3.0, [text_characteristic("Половой признак", "Женская")]),
            "B": ("Куртка", 3.0, [text_characteristic("Половой признак", "Мужская")]),
        }
    )

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="куртка женская"))

    assert [c.code for c in result.candidates] == ["A"]


def test_a_position_silent_on_a_characteristic_survives() -> None:
    """Silence is not disagreement.

    КТРУ positions declare different characteristics. Treating a position that simply has no
    «Сезонность» entry as contradicting «зимняя» eliminated the right answer and left a
    loosely-related one — the bug this test exists to prevent.
    """
    catalog = FakeCatalog(
        {
            "COAT": ("Куртка зимняя из кожи", 3.9, [text_characteristic("Цвет", "Чёрный")]),
            "BOOT": ("Обувь из кожи", 1.0, [text_characteristic("Сезонность", "Зимняя")]),
        }
    )

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="куртка зимняя"))

    codes = [c.code for c in result.candidates]
    assert "COAT" in codes
    # And the far better name match still leads, despite backing fewer characteristics.
    assert codes[0] == "COAT"


def test_a_matched_characteristic_breaks_a_tie_between_similar_names() -> None:
    catalog = FakeCatalog(
        {
            "A": ("Куртка", 3.0, [text_characteristic("Половой признак", "Женская")]),
            "B": ("Куртка", 3.0, [text_characteristic("Цвет", "Синий")]),
        }
    )

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="куртка женская"))

    assert result.candidates[0].code == "A"


def test_every_filter_records_what_it_eliminated() -> None:
    catalog = FakeCatalog(
        {
            "A": ("Куртка", 3.0, [text_characteristic("Половой признак", "Женская")]),
            "B": ("Куртка", 3.0, [text_characteristic("Половой признак", "Мужская")]),
        }
    )

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="куртка женская"))

    # The user has to be able to see how the system got from a description to a code.
    applied = [step for step in result.steps if step.label == "Половой признак"]
    assert applied and applied[0].eliminated == 1


# --- unification and errors ---------------------------------------------------------------


def test_a_unit_is_unified_and_the_change_is_visible() -> None:
    catalog = FakeCatalog({"A": ("Молоток", 3.0, [range_characteristic("Длина", "100", "500")])})

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="молоток 350 мм"))

    (item,) = result.characteristics
    assert (item.value, item.unit, item.raw_unit) == ("350", "Миллиметр", "мм")
    assert item.was_unified


def test_a_number_outside_every_range_is_reported_with_the_allowed_values() -> None:
    catalog = FakeCatalog({"A": ("Молоток", 3.0, [range_characteristic("Длина", "100", "500")])})

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="молоток 9000 мм"))

    (issue,) = [i for i in result.issues if i.kind is IssueKind.OUT_OF_RANGE]
    assert "9000" in issue.raw
    # An error the user cannot act on is worth nothing: the permitted range comes with it.
    assert issue.suggestions


def test_an_unknown_unit_is_reported_as_such() -> None:
    catalog = FakeCatalog({"A": ("Молоток", 3.0, [range_characteristic("Длина", "100", "500")])})

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="молоток 350 попугаев"))

    assert any(i.kind is IssueKind.UNKNOWN_UNIT for i in result.issues)


def test_missing_required_characteristics_are_listed() -> None:
    catalog = FakeCatalog(
        {"A": ("Молоток", 3.0, [text_characteristic("Тип", "Слесарный", required=True)])}
    )

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="молоток"))

    (issue,) = [i for i in result.issues if i.kind is IssueKind.MISSING_REQUIRED]
    assert "Тип" in issue.characteristic


def test_a_correction_is_reported_as_an_issue() -> None:
    catalog = FakeCatalog({"A": ("Молоток", 3.0, [])})

    result = analyser(catalog, FakeCorrector("молоток")).execute(
        AnalyzeDescriptionInput(description="молаток")
    )

    assert result.corrected == "молоток"
    assert any(i.kind is IssueKind.TYPO for i in result.issues)


def test_correction_can_be_switched_off() -> None:
    catalog = FakeCatalog({"A": ("Молоток", 3.0, [])})

    result = analyser(catalog, FakeCorrector("молоток")).execute(
        AnalyzeDescriptionInput(description="молаток", correct_typos=False)
    )

    assert result.corrected == "молаток"
    assert not [i for i in result.issues if i.kind is IssueKind.TYPO]


# --- what to do next ------------------------------------------------------------------------


def test_next_steps_prefer_the_characteristic_that_splits_the_most() -> None:
    catalog = FakeCatalog(
        {
            "A": ("Молоток", 3.0, [text_characteristic("Тип", "a", "b", "c"),
                                   text_characteristic("Цвет", "Чёрный")]),
            "B": ("Молоток", 3.0, [text_characteristic("Тип", "d", "e"),
                                   text_characteristic("Цвет", "Чёрный")]),
        }
    )

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="молоток"))

    # "Цвет" is the same for both and so tells the user nothing; "Тип" separates them.
    assert result.next_steps[0].characteristic == "Тип"


def test_no_next_steps_once_a_single_code_is_left() -> None:
    catalog = FakeCatalog({"A": ("Молоток", 3.0, [text_characteristic("Тип", "a", "b")])})

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="молоток"))

    assert result.state is MatchState.RESOLVED
    assert result.next_steps == []


def test_an_empty_description_returns_nothing_rather_than_everything() -> None:
    catalog = FakeCatalog({"A": ("Молоток", 3.0, [])})

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="   "))

    assert result.state is MatchState.EMPTY
    assert result.candidates == []


@pytest.mark.parametrize("description", ["куртка", "молоток 350 мм", "обувь кожаная"])
def test_the_analyser_never_raises_on_ordinary_input(description: str) -> None:
    catalog = FakeCatalog({"A": ("Молоток", 1.0, [range_characteristic("Длина", "1", "9999")])})

    assert analyser(catalog).execute(AnalyzeDescriptionInput(description=description)) is not None


# --- abstention -----------------------------------------------------------------------------
#
# КТРУ does not cover everything a user will type. Across all 183 organiser archives there is
# no position whose name contains «отвертк» outside the medical group — yet trigram similarity
# cannot return nothing, so it returns the least-bad row. On the live stack that row was
# «Очки для защиты от радиации», reported with `state=resolved`, because being the only
# candidate left was treated as evidence of being the right one.


def test_a_single_unrelated_candidate_is_not_reported_as_resolved() -> None:
    catalog = FakeCatalog({"A": ("Очки для защиты от радиации", 0.51, [])})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="отвертка крестовая PH2")
    )

    assert result.state is MatchState.NOT_FOUND
    assert result.resolved is None


def test_abstention_names_the_nearest_positions() -> None:
    # A dead end the user can act on: nothing matched, but here is what is nearby.
    catalog = FakeCatalog({"A": ("Кровать детская", 0.55, [])})

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="отвертка PH2"))

    (issue,) = [i for i in result.issues if i.kind is IssueKind.NO_MATCH]
    assert issue.suggestions == ["Кровать детская"]


def test_a_named_product_is_answered_however_short_the_description() -> None:
    # The guard must not fire on a good match. «шкаф офисный» names «Шкаф для одежды
    # металлический» — the word is right there — even though the database score for a
    # three-word query is low in absolute terms. An absolute score floor rejected this.
    catalog = FakeCatalog({"A": ("Шкаф для одежды металлический", 0.61, [])})

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="шкаф офисный"))

    assert result.state is MatchState.RESOLVED
    assert result.confidence == 1.0


def test_abstention_offers_no_next_steps() -> None:
    # Advice about a position the user never asked about is what made the wrong answer look
    # considered.
    catalog = FakeCatalog(
        {"A": ("Кровать детская", 0.55, [text_characteristic("Тип", "Одинарная", "Двойная")])}
    )

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="отвертка PH2"))

    assert result.next_steps == []


# --- which characteristic a number belongs to -----------------------------------------------


def test_a_number_goes_to_the_characteristic_named_beside_it() -> None:
    # «шкаф офисный 2000 мм высота» put the 2000 on «Высота стеллажа» — the first millimetre
    # characteristic that happened to accept it. Height, width and depth are all millimetres
    # and all accept 2000; the word «высота» standing next to the number is the only thing
    # that distinguishes them.
    characteristics = [
        range_characteristic("Глубина шкафа", "300", "900"),
        range_characteristic("Высота шкафа", "400", "2400"),
        range_characteristic("Ширина шкафа", "300", "2400"),
    ]
    catalog = FakeCatalog({"A": ("Шкаф для одежды металлический", 3.0, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="шкаф офисный 2000 мм высота")
    )

    assert [(c.name, c.value) for c in result.characteristics] == [("Высота шкафа", "2000")]


def test_the_label_may_stand_before_the_number() -> None:
    # Russian puts it on either side with equal ease.
    characteristics = [
        range_characteristic("Ширина шкафа", "300", "2400"),
        range_characteristic("Высота шкафа", "400", "2400"),
    ]
    catalog = FakeCatalog({"A": ("Шкаф", 3.0, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="шкаф высота 2000 мм")
    )

    assert [c.name for c in result.characteristics] == ["Высота шкафа"]


def test_a_bare_number_with_no_label_and_no_unit_is_not_attributed() -> None:
    # Only that the value happens to be allowed, which is true of hundreds of
    # characteristics. Guessing here files a bare «48» under whichever was checked first.
    catalog = FakeCatalog({"A": ("Шкаф", 3.0, [range_characteristic("Высота шкафа", "1", "100")])})

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="шкаф 48"))

    assert result.characteristics == []


# --- what is wrong with a value, and why ----------------------------------------------------


def test_a_wrong_unit_is_reported_separately_from_a_wrong_value() -> None:
    # «500 г» against a characteristic measured in kilograms is not an out-of-range value, and
    # saying so would send the user looking for a bigger number. The two complaints lead to
    # different fixes, so they are different issues.
    characteristics = [range_characteristic("Масса головки молотка", "0.2", "2", unit=KG)]
    catalog = FakeCatalog({"A": ("Молоток", 3.0, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="молоток масса головки 500 кг")
    )

    (issue,) = [i for i in result.issues if i.kind is IssueKind.OUT_OF_RANGE]
    assert "Масса головки молотка" in issue.message
    # The explanation quotes what КТРУ actually permits, rather than only refusing the value.
    assert any("0.2" in suggestion for suggestion in issue.suggestions)


def test_a_value_is_judged_against_the_characteristic_that_was_named() -> None:
    # Judging against "the first characteristic using this unit" quoted the wrong ranges: a
    # valid height could be refused against a depth range it was never meant to satisfy.
    characteristics = [
        range_characteristic("Глубина шкафа", "300", "900"),
        range_characteristic("Высота шкафа", "400", "2400"),
    ]
    catalog = FakeCatalog({"A": ("Шкаф", 3.0, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="шкаф 2000 мм высота")
    )

    assert [i for i in result.issues if i.kind is IssueKind.OUT_OF_RANGE] == []


def test_a_characteristic_name_after_a_number_is_not_a_missing_unit() -> None:
    # «48 размер» is valid input. Reporting «единица измерения "размер" отсутствует в ОКЕИ»
    # next to it produced a false error on every clothing description.
    characteristics = [text_characteristic("Российский размер", "46", "48", "50")]
    catalog = FakeCatalog({"A": ("Куртка зимняя из кожи", 3.0, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="куртка зимняя 48 размер")
    )

    assert [i for i in result.issues if i.kind is IssueKind.UNKNOWN_UNIT] == []


def test_a_word_that_names_nothing_is_still_reported_as_an_unknown_unit() -> None:
    # The suppression above must not swallow a genuinely invented unit.
    catalog = FakeCatalog({"A": ("Молоток", 3.0, [range_characteristic("Длина", "100", "500")])})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="молоток 350 попугаев")
    )

    assert any(i.kind is IssueKind.UNKNOWN_UNIT for i in result.issues)


# --- correction reporting -------------------------------------------------------------------


def test_each_corrected_word_is_reported_on_its_own() -> None:
    catalog = FakeCatalog({"A": ("Стол письменный", 3.0, [])})
    corrector = FakeCorrector("конструкция сварная стол письменный")

    result = analyser(catalog, corrector).execute(
        AnalyzeDescriptionInput(description="кострукция сварная стол письменный")
    )

    (issue,) = [i for i in result.issues if i.kind is IssueKind.TYPO]
    assert issue.raw == "кострукция"
    assert issue.suggestions == ["конструкция"]


def test_an_inflected_form_is_reported_as_normalisation_not_a_typo() -> None:
    catalog = FakeCatalog({"A": ("Стеллаж офисный", 3.0, [])})
    corrector = FakeCorrector("стул офисный на колесиков")

    result = analyser(catalog, corrector).execute(
        AnalyzeDescriptionInput(description="стул офисный на колесиках")
    )

    assert [i.kind for i in result.issues if i.kind is IssueKind.TYPO] == []
    (issue,) = [i for i in result.issues if i.kind is IssueKind.NORMALIZED]
    assert issue.raw == "колесиках"


# --- unification of units -------------------------------------------------------------------


def test_a_value_is_converted_into_the_unit_ктру_states() -> None:
    # «500 г» against a mass КТРУ expresses in kilograms is 0.5 кг, not a wrong unit.
    characteristics = [range_characteristic("Масса головки молотка", "0.2", "2", unit=KG)]
    catalog = FakeCatalog({"A": ("Молоток", 3.0, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="молоток масса головки 500 г")
    )

    (item,) = result.characteristics
    assert (item.value, item.unit) == ("0.5", "Килограмм")
    # Both forms are kept, so the interface can show what was unified rather than silently
    # replacing what the user wrote.
    assert item.raw == "500 г"
    assert item.raw_unit == "г"
    assert item.was_unified


def test_an_incommensurable_unit_is_still_an_error() -> None:
    # Conversion must not turn every wrong unit into a silent success: a length is not a mass.
    characteristics = [range_characteristic("Масса головки молотка", "0.2", "2", unit=KG)]
    catalog = FakeCatalog({"A": ("Молоток", 3.0, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="молоток масса головки 500 мм")
    )

    assert [i.kind for i in result.issues if i.kind is IssueKind.WRONG_UNIT] == [
        IssueKind.WRONG_UNIT
    ]


# --- telling identical names apart ----------------------------------------------------------


def test_candidates_with_the_same_name_carry_what_differs() -> None:
    # КТРУ holds three positions called «Куртка зимняя из кожи». A list of identical names is
    # not a choice the user can make.
    catalog = FakeCatalog(
        {
            "A": (
                "Куртка зимняя из кожи",
                3.0,
                [text_characteristic("Натуральная кожа", "Да")],
            ),
            "B": (
                "Куртка зимняя из кожи",
                3.0,
                [text_characteristic("Натуральная кожа", "Нет")],
            ),
        }
    )

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="куртка зимняя"))

    first, second = result.candidates
    assert first.distinctions == ["Натуральная кожа: Да"]
    assert second.distinctions == ["Натуральная кожа: Нет"]


def test_a_characteristic_all_candidates_share_is_not_a_distinction() -> None:
    # Telling the user that both positions are «Зима» does not help them pick one.
    shared = text_characteristic("Сезонность", "Зима")
    catalog = FakeCatalog(
        {
            "A": ("Куртка", 3.0, [shared, text_characteristic("Капюшон", "Да")]),
            "B": ("Куртка", 3.0, [shared, text_characteristic("Капюшон", "Нет")]),
        }
    )

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="куртка"))

    assert all(
        not any(line.startswith("Сезонность") for line in candidate.distinctions)
        for candidate in result.candidates
    )


def test_a_single_candidate_needs_no_distinctions() -> None:
    catalog = FakeCatalog({"A": ("Молоток", 3.0, [text_characteristic("Тип", "Слесарный")])})

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="молоток"))

    assert result.candidates[0].distinctions == []


def test_abstention_reports_no_characteristics() -> None:
    # Every extracted characteristic was attributed by looking at the candidate positions, and
    # those are the positions just judged not to be the product. «Высота изножья (от спального
    # места): 150 мм» for a screwdriver is a confident statement about a bed.
    characteristics = [range_characteristic("Высота изножья", "100", "500")]
    catalog = FakeCatalog({"A": ("Кровать детская", 0.55, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="отвертка крестовая 150 мм")
    )

    assert result.state is MatchState.NOT_FOUND
    assert result.characteristics == []
    # The search itself still reported: how many names were looked at, and that none fit.
    assert [step.label for step in result.steps] == ["Поиск по наименованию"]


def test_abstention_does_not_offer_the_same_name_twice() -> None:
    # КТРУ gives several positions the same name; three identical chips look like three
    # choices and are not.
    catalog = FakeCatalog(
        {
            "A": ("Кровать детская", 0.55, []),
            "B": ("Кровать детская", 0.54, []),
            "C": ("Стол обеденный", 0.53, []),
        }
    )

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="отвертка PH2"))

    (issue,) = [i for i in result.issues if i.kind is IssueKind.NO_MATCH]
    assert issue.suggestions == ["Кровать детская", "Стол обеденный"]


def test_a_named_characteristic_claims_its_number_even_when_the_value_is_wrong() -> None:
    # «матрац длина 400 см» against «Длина» permitted 60–250: attribution used to require the
    # value to be *acceptable*, so 400 could not land on «Длина» and silently landed on
    # «Высота», whose range is open. The one real error in the description disappeared — and
    # «Длина» was then reported as a *missing* characteristic the user had just supplied.
    characteristics = [
        range_characteristic("Длина", "60", "250", required=True),
        range_characteristic("Высота", "1", "100000"),
    ]
    catalog = FakeCatalog({"A": ("Матрац", 3.0, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="матрац длина 400 мм")
    )

    assert [(c.name, c.value) for c in result.characteristics] == [("Длина", "400")]
    (issue,) = [i for i in result.issues if i.kind is IssueKind.OUT_OF_RANGE]
    assert issue.characteristic == "Длина"
    # And it is no longer claimed to be missing — the user supplied it.
    assert not [
        i
        for i in result.issues
        if i.kind is IssueKind.MISSING_REQUIRED and "Длина" in i.characteristic
    ]


def test_an_attributed_value_inside_the_range_raises_nothing() -> None:
    # The new check must not turn every attributed value into a complaint.
    characteristics = [range_characteristic("Длина", "60", "250")]
    catalog = FakeCatalog({"A": ("Матрац", 3.0, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="матрац длина 200 мм")
    )

    assert [(c.name, c.value) for c in result.characteristics] == [("Длина", "200")]
    assert [i for i in result.issues if i.kind is IssueKind.OUT_OF_RANGE] == []


def test_a_number_is_not_attributed_to_a_qualitative_characteristic() -> None:
    # A number is not one of an enumerated list of texts, and letting a label drag it there
    # would file «тип 5» under «Тип: Слесарный».
    catalog = FakeCatalog({"A": ("Молоток", 3.0, [text_characteristic("Тип", "Слесарный")])})

    result = analyser(catalog).execute(AnalyzeDescriptionInput(description="молоток тип 5"))

    assert result.characteristics == []


def test_the_products_own_name_is_not_a_label_for_a_number() -> None:
    # «молоток слесарный 350 попугаев» filed the 350 under «Масса головки молотка»: the word
    # «молоток» occurs in that characteristic's name, so the product's own noun acted as a
    # label and claimed a number that belongs to nothing. A word identifying the product is
    # evidence about every one of its characteristics equally — which is no evidence at all.
    characteristics = [range_characteristic("Масса головки молотка", "0.2", "2", unit=KG)]
    catalog = FakeCatalog({"A": ("Молоток", 3.0, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="молоток слесарный 350 попугаев")
    )

    assert result.characteristics == []
    # And with nothing claiming it, the invented unit is reported, as it should be.
    assert any(i.kind is IssueKind.UNKNOWN_UNIT for i in result.issues)


def test_a_genuine_label_still_claims_its_number() -> None:
    # The product-name guard must not disarm labelling itself.
    characteristics = [range_characteristic("Масса головки молотка", "0.2", "2", unit=KG)]
    catalog = FakeCatalog({"A": ("Молоток", 3.0, characteristics)})

    result = analyser(catalog).execute(
        AnalyzeDescriptionInput(description="молоток масса головки 0.5 кг")
    )

    assert [(c.name, c.value) for c in result.characteristics] == [
        ("Масса головки молотка", "0.5")
    ]
