"""Pulling characteristic values out of a free-text description.

Two complementary passes, because КТРУ expresses characteristics in two different ways:

* **qualitative** — the permitted values are an enumerated list of texts, so the description is
  scanned for those exact texts («Пластик», «Женский», «Да»). Matching against the reference
  rather than guessing at grammar is what keeps this reliable on Russian, where a naive parser
  would have to handle every case ending.
* **quantitative** — the value is a number with a unit, so the description is scanned for
  ``число + единица`` pairs and each is offered to the characteristics that use that unit.

A number alone says nothing about *which* characteristic it belongs to, and the unit narrows it
only to "everything measured in millimetres" — which for a wardrobe is height, width, depth and
a dozen more. The words standing next to the number are the missing evidence, so each mention
carries its own context and :func:`label_affinity` scores characteristic names against it. That
is what separates «шкаф офисный 2000 мм высота» → «Высота шкафа» from the same number landing
on «Высота стеллажа» because that characteristic happened to be checked first.

Pure functions over domain objects: no ORM, no HTTP, no Django. That is what makes the
narrowing testable without a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation

from apps.ktru.domain.conversion import convert
from apps.ktru.domain.entities import Characteristic
from apps.ktru.domain.enums import ValueKind

#: A number followed by an optional unit word. The comma decimal separator is normal in
#: Russian, and `мм`/`кг` sit flush against the digits as often as not.
_NUMBER_UNIT = re.compile(
    r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*([а-яёa-z°%]{1,24}(?:/[а-яёa-z]{1,8})?)?",
    re.IGNORECASE,
)

#: Any run of letters — used to collect the words around a number.
_WORD = re.compile(r"[а-яёa-z]+", re.IGNORECASE)

#: Words that look like units to the regex but are ordinary language.
_NOT_UNITS = {"и", "в", "на", "для", "из", "с", "по", "шт1", "год", "лет"}

#: How many words on each side of a number count as its context.
#:
#: Three before and two after: Russian puts the label on either side with equal ease — «высота
#: 2000 мм» and «2000 мм высота» are both natural — and a window wider than this starts picking
#: up the label of the *next* number in a list of dimensions.
CONTEXT_BEFORE = 3
CONTEXT_AFTER = 2

#: Characters compared when matching a word against a characteristic's name.
#:
#: Russian inflects: the description says «шкаф», the characteristic is named «Высота шкафа»,
#: and «высота» appears as «высоту», «высотой». A prefix of five characters makes those the
#: same word without a morphological analyser — which, for matching against a fixed reference,
#: would be a heavy dependency bought for nothing. Five is long enough that «шкаф» and «шкала»
#: stay apart.
STEM_LENGTH = 5

#: Name words too generic to be evidence of anything: they appear in hundreds of characteristic
#: names, so matching on them would give every characteristic the same affinity.
_STOP_NAME_WORDS = {"товар", "издел", "матер", "общий", "основ", "налич", "тип", "вид"}


@dataclass(frozen=True)
class NumberMention:
    """A number found in the description, with everything known about where it stood."""

    raw: str
    number: Decimal
    raw_unit: str
    unit: str
    #: Words standing near the number, stemmed and lowercased — its evidence of meaning.
    context: tuple[str, ...] = field(default_factory=tuple)

    @property
    def unit_is_unknown(self) -> bool:
        """A word stands where a unit belongs, and ОКЕИ does not have it.

        Whether that is actually an *error* needs one more piece of information this object
        does not have — see :func:`names_a_characteristic`. «48 размер» and «350 попугаев» are
        indistinguishable here: both are a number followed by a word ОКЕИ never heard of. The
        difference is that «размер» is the name of a characteristic and «попугаев» is not.
        """
        return bool(self.raw_unit) and not self.unit


@dataclass(frozen=True)
class TextMention:
    characteristic: str
    raw: str
    value: str


def stem(word: str) -> str:
    """The comparable core of a word. See :data:`STEM_LENGTH`."""
    return word.casefold()[:STEM_LENGTH]


def label_affinity(
    name: str, context: tuple[str, ...], ignore: frozenset[str] = frozenset()
) -> int:
    """How many of a characteristic name's words stand next to the number.

    The score is a count, not a ratio, so a two-word match beats a one-word match even when the
    longer name has more words left over: for «шкаф офисный 2000 мм высота», «Высота шкафа»
    matches both of its words and «Высота стеллажа» only one, which is exactly the distinction
    that was missing.

    ``ignore`` holds the stems that name the *product* rather than any of its attributes.
    Without it, «молоток слесарный 350 попугаев» scored «Масса головки молотка» at 1 — the
    word «молоток» appears in that characteristic's name, so the product's own noun acted as
    a label and claimed a number that belongs to nothing. A word that identifies the product
    is evidence about every one of its characteristics equally, which is to say no evidence
    at all.
    """
    if not context:
        return 0
    wanted = {
        token
        for token in (stem(word) for word in _WORD.findall(name))
        if len(token) >= 3 and token not in _STOP_NAME_WORDS and token not in ignore
    }
    return len(wanted & set(context))


def names_a_characteristic(word: str, characteristics: list[Characteristic]) -> bool:
    """Whether a word is the name of one of these characteristics rather than a unit.

    This is what tells «48 размер» apart from «350 попугаев». Both put a word ОКЕИ does not
    know after a number, so neither can be unified — but «размер» is the user naming
    «Российский размер», and complaining about it is a false error, while «попугаев» names
    nothing and is exactly the "несуществующее значение" the task asks to be caught.
    """
    needle = stem(word)
    if len(needle) < 3:
        return False
    return any(
        needle in {stem(part) for part in _WORD.findall(characteristic.name)}
        for characteristic in characteristics
    )


def find_numbers(text: str, normalize_unit) -> list[NumberMention]:
    """Every ``число + единица`` in the description, with the unit mapped onto ОКЕИ."""
    words = [(match.start(), match.group(0)) for match in _WORD.finditer(text)]

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
                # The unit word is suppressed from the context only when it really was a
                # unit. «48 размер» captures «размер» in the unit slot because that is where
                # it stands, but ОКЕИ does not have it — and it is the single best clue to
                # which characteristic the 48 belongs to, so it has to stay in the context.
                context=_context(words, match.start(), match.end(), raw_unit if unit else ""),
            )
        )
    return mentions


def _context(
    words: list[tuple[int, str]], start: int, end: int, raw_unit: str
) -> tuple[str, ...]:
    """The stemmed words around one number.

    The unit word itself is dropped: it is already captured as the unit, and leaving it in the
    context would let «мм» match the stem of a characteristic named «Миллиметровая разметка».
    """
    unit_stem = stem(raw_unit) if raw_unit else ""
    before = [stem(word) for offset, word in words if offset < start]
    after = [stem(word) for offset, word in words if offset >= end]
    picked = before[-CONTEXT_BEFORE:] + after[:CONTEXT_AFTER]
    return tuple(token for token in picked if len(token) >= 3 and token != unit_stem)


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


def in_characteristic_units(
    characteristic: Characteristic, mention: NumberMention
) -> NumberMention | None:
    """The mention rewritten into a unit this characteristic is measured in.

    This is where "приведение к унифицированному виду" actually happens for numbers. КТРУ
    states each characteristic's bounds in one unit; a supplier writes whichever unit is
    natural. «500 г» against a mass stated in kilograms is not a wrong unit, it is 0.5 кг — and
    the converted mention keeps the original text in ``raw``, so the interface can show both
    what was written and what it became.

    ``None`` means the units are genuinely incommensurable: 100 мм is not a mass, and no
    rewriting makes it one. That is a real error, reported as such.
    """
    units = {unit.name for unit in characteristic.units}
    # No unit on either side: nothing to reconcile, and a unitless characteristic accepts the
    # number on the strength of its value alone.
    if not units or not mention.unit:
        return mention
    if mention.unit in units:
        return mention

    # Deterministic order, so a characteristic measured in both metres and millimetres always
    # converts to the same one of them.
    for target in sorted(units):
        converted = convert(mention.number, mention.unit, target)
        if converted is not None:
            return replace(mention, number=converted, unit=target)
    return None


def characteristic_accepts(characteristic: Characteristic, mention: NumberMention) -> bool:
    """Whether a numeric mention satisfies one of this characteristic's permitted values."""
    expressed = in_characteristic_units(characteristic, mention)
    if expressed is None:
        return False
    return any(value.contains(expressed.number) for value in characteristic.values)


def unit_is_compatible(characteristic: Characteristic, mention: NumberMention) -> bool:
    """Whether the mention's unit can be expressed in one this characteristic uses.

    Separate from :func:`characteristic_accepts` because the two failures mean different things
    to the user: a wrong unit is «эта характеристика измеряется в миллиметрах», an out-of-range
    value is «допустимо от 400 до 2400». Collapsing them into one boolean made it impossible to
    say which had happened.
    """
    return in_characteristic_units(characteristic, mention) is not None


def characteristic_accepts_text(characteristic: Characteristic, value: str) -> bool:
    folded = value.casefold()
    return any(
        permitted.kind is ValueKind.TEXT and permitted.text.casefold() == folded
        for permitted in characteristic.values
    )
