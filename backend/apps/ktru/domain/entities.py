"""КТРУ reference entities. No Django in this layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from apps.ktru.domain.enums import CharacteristicType, ValueKind


@dataclass(frozen=True)
class Unit:
    """An ОКЕИ unit of measure: the standard the task asks values to be mapped onto."""

    code: str
    name: str


@dataclass(frozen=True)
class PermittedValue:
    """One value a characteristic is allowed to take.

    Qualitative characteristics enumerate texts; quantitative ones give either an exact number
    or an interval, each with a unit. Checking a user's value against these is what turns
    "несуществующее значение" from a guess into a statement with a reason attached.
    """

    kind: ValueKind
    text: str = ""
    exact: Decimal | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    min_inclusive: bool = True
    max_inclusive: bool = True
    unit: Unit | None = None

    def contains(self, number: Decimal) -> bool:
        """Whether a number satisfies this value. Texts never do."""
        if self.kind is ValueKind.EXACT:
            return self.exact is not None and number == self.exact
        if self.kind is ValueKind.RANGE:
            if self.minimum is not None:
                if number < self.minimum or (number == self.minimum and not self.min_inclusive):
                    return False
            if self.maximum is not None:
                if number > self.maximum or (number == self.maximum and not self.max_inclusive):
                    return False
            return True
        return False

    def describe(self) -> str:
        """Human-readable form, for the explanation shown next to a rejected value."""
        if self.kind is ValueKind.TEXT:
            return self.text
        unit = f" {self.unit.name}" if self.unit else ""
        if self.kind is ValueKind.EXACT:
            return f"{_trim(self.exact)}{unit}"
        low = (
            ""
            if self.minimum is None
            else f"{'≥' if self.min_inclusive else '>'} {_trim(self.minimum)}"
        )
        high = (
            ""
            if self.maximum is None
            else f"{'≤' if self.max_inclusive else '<'} {_trim(self.maximum)}"
        )
        if low and high:
            return f"{low} … {high}{unit}".strip()
        return f"{low or high}{unit}"


def _trim(number: Decimal | None) -> str:
    """Drop a trailing ``.0`` so ``35.0 Миллиметр`` reads as ``35 Миллиметр``."""
    if number is None:
        return ""
    normalized = number.normalize()
    return format(normalized, "f")


@dataclass(frozen=True)
class Characteristic:
    code: str
    name: str
    type: CharacteristicType
    is_required: bool
    values: list[PermittedValue] = field(default_factory=list)

    @property
    def is_quantitative(self) -> bool:
        return self.type is CharacteristicType.QUANTITATIVE

    @property
    def units(self) -> list[Unit]:
        """The distinct units this characteristic is expressed in."""
        seen: dict[str, Unit] = {}
        for value in self.values:
            if value.unit:
                seen.setdefault(value.unit.code, value.unit)
        return list(seen.values())


@dataclass(frozen=True)
class Position:
    """One КТРУ position — the code the whole flow is trying to arrive at."""

    code: str
    name: str
    okpd2_code: str
    okpd2_name: str
    characteristics: list[Characteristic] = field(default_factory=list)
