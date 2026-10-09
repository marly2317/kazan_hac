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
from apps.ktru.domain.entities import Characteristic, PermittedValue, Unit
from apps.ktru.domain.enums import CharacteristicType, ValueKind
from apps.spellcheck.domain.correction import Correction
from apps.spellcheck.domain.enums import CorrectionSource

MM = Unit(code="003", name="Миллиметр")
KG = Unit(code="166", name="Килограмм")

UNITS = {"мм": "Миллиметр", "kg": "Килограмм", "кг": "Килограмм"}


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

    def find_positions(self, text: str, *, limit: int) -> list[PositionCandidate]:
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
