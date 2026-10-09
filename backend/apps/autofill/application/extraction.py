"""Pulling characteristic values out of a free-text description.

Two complementary passes, because КТРУ expresses characteristics in two different ways:

* **qualitative** — the permitted values are an enumerated list of texts, so the description is
  scanned for those exact texts («Пластик», «Женский», «Да»). Matching against the reference
  rather than guessing at grammar is what keeps this reliable on Russian, where a naive parser
  would have to handle every case ending.
* **quantitative** — the value is a number with a unit, so the description is scanned for
  ``число + единица`` pairs and each is offered to the characteristics that use that unit.

Pure functions over domain objects: no ORM, no HTTP, no Django. That is what makes the
narrowing testable without a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from apps.ktru.domain.entities import Characteristic
from apps.ktru.domain.enums import ValueKind

#: A number followed by an optional unit word. The comma decimal separator is normal in
#: Russian, and `мм`/`кг` sit flush against the digits as often as not.
_NUMBER_UNIT = re.compile(
    r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*([а-яёa-z°%]{1,24}(?:/[а-яёa-z]{1,8})?)?",
    re.IGNORECASE,
)

#: Words that look like units to the regex but are ordinary language.
_NOT_UNITS = {"и", "в", "на", "для", "из", "с", "по", "шт1", "год", "лет"}


@dataclass(frozen=True)
class NumberMention:
    raw: str
    number: Decimal
    raw_unit: str
    unit: str


@dataclass(frozen=True)
class TextMention:
    characteristic: str
    raw: str
    value: str


def find_numbers(text: str, normalize_unit) -> list[NumberMention]:
    """Every ``число + единица`` in the description, with the unit mapped onto ОКЕИ."""
    mentions: list[NumberMention] = []
    for match in _NUMBER_UNIT.finditer(text):
        raw_number, raw_unit = match.group(1), (match.group(2) or "").strip()
        try:
            number = Decimal(raw_number.replace(",", "."))
        except InvalidOperation:
            continue
        if raw_unit.casefold() in _NOT_UNITS:
            raw_unit = ""
        unit = normalize_unit(raw_unit) if raw_unit else None
        mentions.append(
            NumberMention(
                raw=match.group(0).strip(),
                number=number,
                raw_unit=raw_unit,
                unit=unit or "",
            )
        )
    return mentions


def find_texts(text: str, characteristics: list[Characteristic]) -> list[TextMention]:
    """Permitted qualitative values that appear verbatim in the description.

    Longest values are tried first so «Мужской костюм» is not matched as «Мужской» when both
    are permitted — the more specific value is the more informative one.
    """
    haystack = text.casefold()
    found: list[TextMention] = []
    seen: set[tuple[str, str]] = set()

    options: list[tuple[str, str]] = []
    for characteristic in characteristics:
        for value in characteristic.values:
            if value.kind is ValueKind.TEXT and value.text:
                options.append((characteristic.name, value.text))

    for name, value in sorted(options, key=lambda pair: -len(pair[1])):
        key = (name, value.casefold())
        if key in seen:
            continue
        needle = value.casefold()
        # Word-boundary check, so «Да» does not match inside «Дальность».
        match = re.search(
            rf"(?<![0-9а-яёa-z]){re.escape(needle)}(?![0-9а-яёa-z])", haystack
        )
        if not match:
            continue
        seen.add(key)
        found.append(TextMention(characteristic=name, raw=value, value=value))
        # Blank out the span so a shorter permitted value cannot match the same words again:
        # «мужской костюм» must yield «Мужской костюм», not that *and* «Мужской». NUL is used
        # because no permitted value can contain it, so nothing matches across the gap.
        haystack = (
            haystack[: match.start()]
            + "\x00" * (match.end() - match.start())
            + haystack[match.end() :]
        )
    return found


def characteristic_accepts(characteristic: Characteristic, mention: NumberMention) -> bool:
    """Whether a numeric mention satisfies one of this characteristic's permitted values."""
    units = {unit.name for unit in characteristic.units}
    # A unit the characteristic does not use means the mention is about something else —
    # 100 мм is not a candidate for a characteristic measured in kilograms.
    if units and mention.unit and mention.unit not in units:
        return False
    return any(value.contains(mention.number) for value in characteristic.values)


def characteristic_accepts_text(characteristic: Characteristic, value: str) -> bool:
    folded = value.casefold()
    return any(
        permitted.kind is ValueKind.TEXT and permitted.text.casefold() == folded
        for permitted in characteristic.values
    )
