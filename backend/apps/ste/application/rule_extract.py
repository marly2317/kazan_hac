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


#: Слово значения, по которому значение узнаётся в одиночку, должно быть не короче этого:
#: «углошлифовальная» узнаёт «Углошлифовальная машина», а «машина» — нет.
DISTINCT_WORD = 8


def _find_text_value(text: str, char: FamilyChar) -> tuple[str, str] | None:
    """Допустимое значение, названное в тексте (по основам слов).

    Сначала — значение, все слова которого есть в тексте (самое длинное). Иначе — значение с
    длинным словом, которое есть в тексте и не встречается ни в одном другом значении этой
    характеристики: «углошлифовальная» однозначно называет «Углошлифовальная машина».
    """
    text_stems = _stems(text)
    joined = " " + " ".join(text_stems) + " "
    words = {word.casefold().replace("ё", "е") for word in _WORD.findall(text)}
    best: tuple[str, str] | None = None
    values = char.text_values()
    for value in values:
        value_stems = [stem for stem in _stems(value) if len(stem) >= 3]
        if not value_stems:
            continue
        needle = " " + " ".join(value_stems) + " "
        if needle in joined and (best is None or len(value) > len(best[0])):
            best = (value, _quote_for(text, value_stems))
    if best is not None:
        return best
    candidates = []
    for value in values:
        for word in _WORD.findall(value):
            folded = word.casefold().replace("ё", "е")
            if len(folded) < DISTINCT_WORD or folded[:-2] not in " ".join(words):
                continue
            if not any(_stem_in(folded, other) for other in values if other != value):
                candidates.append((value, word))
    if len({value for value, _ in candidates}) == 1:
        value, word = candidates[0]
        return value, _quote_for(text, [_stem(word)])
    return None


def _stem_in(word: str, value: str) -> bool:
    return word[:-2] in value.casefold().replace("ё", "е")


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


#: Как поставщики называют габарит, которого в КТРУ нет под этим именем: «ширина» шкафа или
#: стола в КТРУ — «Длина».
HINT_SYNONYMS = {"ширин": "длин"}


def _attribute(
    text: str, position: int, unit: U.Unit | None, numeric: list[FamilyChar], taken: set[str]
) -> FamilyChar | None:
    """К какой характеристике относится число: ближайшее слово-подсказка слева, иначе — размерность.

    Только ближайшее слово: в «высота 215 см ширина 130 см» подсказка к 130 — «ширина», а не
    «высота», стоящая раньше. Из подходящих — характеристика самого изделия («Длина шкафа»),
    а не его части («Длина выдвижного ящика»): часть пользователь назвал бы.
    """
    words = [w for w in _WORD.findall(text[max(0, position - 40) : position]) if len(w) >= 4]
    hint = _stem(words[-1]) if words else ""
    hint = HINT_SYNONYMS.get(hint[:5], hint)
    candidates = [char for char in numeric if char.key not in taken]
    if unit is not None:
        candidates = [c for c in candidates if c.unit is None or c.unit.dimension == unit.dimension]
    named = [c for c in candidates if hint and any(st.startswith(hint) for st in _stems(c.name))]
    if named:
        return min(named, key=lambda c: len(c.name.split()))
    if unit is None:
        return None
    same = [c for c in candidates if c.unit is not None]
    return same[0] if len(same) == 1 else None


def _fold(text: str) -> str:
    return fold_text(text)
