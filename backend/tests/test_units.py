"""ОКЕИ unit normalisation — «кг» is not a misspelling of «Килограмм», it is an abbreviation."""

from __future__ import annotations

import pytest

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
