"""Разбор текста по схеме семейства **без LLM** — запасной путь (деградация, архитектура §5).

Когда языковая модель недоступна (нет ключа, исчерпан лимит, сеть), сервис обязан продолжить
работу: найти в тексте допустимые значения семейства дословно (с учётом словоформ) и числа с
единицами, привязав их к характеристикам по слову-подсказке рядом или по размерности единицы.
Это слабее LLM-2 — синонимы («болгарка» → «Углошлифовальная машина») так не взять, — но
честно: каждое значение подкреплено цитатой из текста.
"""

from __future__ import annotations

import re

from apps.ste.domain import units as U
from apps.ste.domain.kb import Family, FamilyChar
from apps.ste.domain.model import Confidence, ExtractedValue, Source, Status
from apps.ste.domain.values import fold_text

_WORD = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
_NUMBER_UNIT = re.compile(
    r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*("
    + "|".join(re.escape(word) for word in U.known_unit_words() if word and not word.isspace())
    + r")?(?![a-zа-яё])",
    re.IGNORECASE,
)

#: Сколько первых букв слова считаются его основой при сравнении словоформ.
STEM = 5


def _stem(word: str) -> str:
    word = word.casefold().replace("ё", "е")
    return word[:STEM] if len(word) > STEM else word


def _stems(text: str) -> list[str]:
    return [_stem(word) for word in _WORD.findall(text)]


def _find_text_value(text: str, char: FamilyChar) -> tuple[str, str] | None:
    """Самое длинное допустимое значение, все слова которого есть в тексте (по основам)."""
    text_stems = _stems(text)
    joined = " " + " ".join(text_stems) + " "
    best: tuple[str, str] | None = None
    for value in char.text_values():
        value_stems = [stem for stem in _stems(value) if len(stem) >= 3]
        if not value_stems:
            continue
        needle = " " + " ".join(value_stems) + " "
        if needle in joined:
            if best is None or len(value) > len(best[0]):
                best = (value, _quote_for(text, value_stems))
    return best


def _quote_for(text: str, stems: list[str]) -> str:
    words = list(_WORD.finditer(text))
    for index, match in enumerate(words):
        if _stem(match.group(0)) == stems[0]:
            end_index = min(index + len(stems) - 1, len(words) - 1)
            return text[match.start() : words[end_index].end()]
    return stems[0]


def extract_by_rules(text: str, family: Family) -> dict[str, ExtractedValue]:
    found: dict[str, ExtractedValue] = {}

    for char in family.chars.values():
        if char.is_numeric:
            continue
        hit = _find_text_value(text, char)
        if hit is None:
            continue
        value, quote = hit
        found[char.key] = ExtractedValue(
            char_key=char.key,
            status=Status.SPECIFIED,
            value=[value] if char.kinds == (3,) else value,
            quote=quote,
            quote_found=True,
            source=Source.TEXT,
            confidence=Confidence.CONFIDENT,
        )

    numeric = [char for char in family.chars.values() if char.is_numeric]
    taken: set[str] = set()
    for match in _NUMBER_UNIT.finditer(text):
        number = float(match.group(1).replace(",", "."))
        unit_text = match.group(2)
        unit = U.parse_unit(unit_text) if unit_text else None
        target = _attribute(text, match.start(), unit, numeric, taken)
        if target is None:
            continue
        taken.add(target.key)
        found[target.key] = ExtractedValue(
            char_key=target.key,
            status=Status.SPECIFIED,
            value=number,
            unit=unit_text,
            quote=match.group(0).strip(),
            quote_found=True,
            source=Source.TEXT,
            confidence=Confidence.CONFIDENT,
        )
    return found


def _attribute(
    text: str, position: int, unit: U.Unit | None, numeric: list[FamilyChar], taken: set[str]
) -> FamilyChar | None:
    """К какой характеристике относится число: слово-подсказка слева, иначе — размерность."""
    left = text[max(0, position - 60) : position]
    left_stems = set(_stems(left)[-4:])
    candidates = [char for char in numeric if char.key not in taken]
    named = [
        char
        for char in candidates
        if left_stems & {stem for stem in _stems(char.name) if len(stem) >= 4}
    ]
    if unit is not None:
        named = [
            char for char in named if char.unit is None or char.unit.dimension == unit.dimension
        ]
    if len(named) >= 1:
        return named[0]
    if unit is None:
        return None
    same = [
        char
        for char in candidates
        if char.unit is not None and char.unit.dimension == unit.dimension
    ]
    if len(same) == 1:
        return same[0]
    return None


def _fold(text: str) -> str:
    return fold_text(text)
