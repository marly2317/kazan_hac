"""ОКЕИ unit normalisation — «кг» is not a misspelling of «Килограмм», it is an abbreviation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.ktru.domain.conversion import are_commensurable, convert, dimension_of
from apps.ktru.infrastructure.units import canonical_unit


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("кг", "Килограмм"),
        ("КГ", "Килограмм"),
        ("Кг.", "Килограмм"),
        ("  кг  ", "Килограмм"),
        # Typed with the keyboard still in the English layout.
        ("kg", "Килограмм"),
        ("mm", "Миллиметр"),
        ("мм", "Миллиметр"),
        ("шт", "Штука"),
        ("штук", "Штука"),
        ("квт", "Киловатт"),
    ],
)
def test_abbreviations_map_onto_the_okei_name(written: str, expected: str) -> None:
    assert canonical_unit(written) == expected


@pytest.mark.parametrize("written", ["", "   ", "попугай", "flurble"])
def test_an_unknown_unit_is_reported_as_unknown(written: str, db) -> None:
    # `None`, not the input: the caller has to be able to say "this is not a unit", which is
    # one of the "несуществующие значения" the task asks to be caught.
    assert canonical_unit(written) is None


# --- conversion between commensurable units -------------------------------------------------
#
# КТРУ states each characteristic's bounds in one unit; a supplier writes whichever unit is
# natural. «500 г» for a hammer head whose mass КТРУ expresses in kilograms is not a wrong
# unit, it is 0.5 кг, and refusing it tells the user something false.


def test_grams_convert_to_kilograms_exactly() -> None:
    assert convert(Decimal("500"), "Грамм", "Килограмм") == Decimal("0.5")


def test_millimetres_convert_to_metres_without_exponent_notation() -> None:
    # `Decimal.normalize()` renders 2000 as `2E+3`, which would reach the user as a value.
    assert str(convert(Decimal("2000"), "Миллиметр", "Метр")) == "2"


def test_conversion_is_symmetric() -> None:
    assert convert(Decimal("2"), "Метр", "Миллиметр") == Decimal("2000")


def test_an_identical_unit_passes_the_value_through() -> None:
    assert convert(Decimal("350"), "Миллиметр", "Миллиметр") == Decimal("350")


def test_units_of_different_dimensions_do_not_convert() -> None:
    # A mass is not a length, and returning anything here would invent a value.
    assert convert(Decimal("100"), "Миллиметр", "Килограмм") is None
    assert not are_commensurable("Миллиметр", "Килограмм")


def test_an_unknown_unit_does_not_convert() -> None:
    # Better no answer than an approximate factor the reference never stated.
    assert convert(Decimal("1"), "Попугай", "Метр") is None
    assert dimension_of("Попугай") is None
