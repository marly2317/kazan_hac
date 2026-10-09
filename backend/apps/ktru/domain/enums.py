"""КТРУ's own vocabulary of kinds and formats, named instead of left as magic numbers."""

from enum import StrEnum


class CharacteristicType(StrEnum):
    """``<oos:type>`` on a characteristic."""

    #: Качественная: the value is one of an enumerated list of texts ("Пластик", "Да").
    QUALITATIVE = "1"
    #: Количественная: the value is a number, with a unit, and often a permitted range.
    QUANTITATIVE = "2"


class ValueKind(StrEnum):
    """How a permitted value is expressed."""

    #: A single allowed text, from ``<oos:qualityDescription>``.
    TEXT = "text"
    #: One exact number, from ``<oos:valueSet><oos:concreteValue>``.
    EXACT = "exact"
    #: An interval, from ``<oos:rangeSet><oos:valueRange>``.
    RANGE = "range"


#: ``<oos:status>`` worth importing. КТРУ keeps superseded positions in the same dump, and
#: matching against a withdrawn code would hand the user a code they cannot actually use.
ACTIVE_STATUS = "ACTIVE"
