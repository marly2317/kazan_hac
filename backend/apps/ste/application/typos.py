"""Исправление опечаток и раскладки по словарю КТРУ (шаг 0 пайплайна).

Стадии взяты из прежней версии (``apps/spellcheck``): раскладка клавиатуры и SymSpell со всеми
защитами правильного ввода — не трогать короткие слова, служебные, слова с цифрами, требовать
совпадения первой буквы. Новое здесь — **словарь**: он собирается из базы знаний (семейства,
листья, характеристики, допустимые значения, единицы ОКЕИ) и синонимов семейств, а не из
Postgres. Исправлять имеет смысл только к словам, которые КТРУ знает: исправление к слову,
которого в справочнике нет, уводит от кода.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from apps.spellcheck.domain.correction import tokenize
from apps.spellcheck.infrastructure.keyboard import KeyboardLayoutRewriter
from apps.spellcheck.infrastructure.symspell_index import SymSpellRewriter
from apps.ste.domain import units as U
from apps.ste.domain.kb import KnowledgeBase
from apps.ste.domain.model import TypoFix

WEIGHT_CHARACTERISTIC = 6
WEIGHT_VALUE = 5
WEIGHT_UNIT = 4
WEIGHT_SYNONYM = 3
WEIGHT_NAME = 2

#: Частые правильные слова поставщиков, которых нет в КТРУ дословно. Без них SymSpell «исправил»
#: бы их к ближайшему слову справочника. Список короткий намеренно: всё остальное приходит из
#: синонимов семейств.
SUPPLIER_WORDS = (
    "болгарка аккумуляторный аккумуляторная сетевой сетевая офисный офисная офисное "
    "зимняя зимний летняя летний мужская мужской женская женский детская детский размер "
    "цвет черный черная белый белая серый синий красный зеленый ткань кожзам экокожа "
    "хлопок полиэстер пластик металл дерево ящик ящика ящиками полки полка дверцы двери "
    "комплект набор кейс зарядка зарядное мощность диаметр длина ширина высота глубина "
    "вес масса объем литров литра рост утепленная утеплённая спецодежда рабочий рабочая"
).split()

MIN_TERM = 3
_WORD = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)


class KbVocabulary:
    """``(слово, частота)`` и биграммы по базе знаний — источник для SymSpell и раскладки."""

    def __init__(self, kb: KnowledgeBase) -> None:
        terms: Counter[str] = Counter()
        pairs: Counter[str] = Counter()

        def add(text: str | None, weight: int) -> None:
            tokens = tokenize(text or "")
            for token in tokens:
                if len(token) >= MIN_TERM and not token.isdigit():
                    terms[token] += weight
            for left, right in zip(tokens, tokens[1:], strict=False):
                pairs[f"{left} {right}"] += weight

        for family in kb.families.values():
            add(family.name, WEIGHT_NAME)
            for word in (*family.synonyms, *family.keywords):
                add(word, WEIGHT_SYNONYM)
            for char in family.chars.values():
                add(char.name, WEIGHT_CHARACTERISTIC)
                for text in char.text_values():
                    add(text, WEIGHT_VALUE)
        for leaf in kb.leaves.values():
            add(leaf.name, WEIGHT_NAME)
        for unit in U.UNITS:
            add(unit.name, WEIGHT_UNIT)
            for synonym in unit.synonyms:
                add(synonym, WEIGHT_UNIT)
        for word in SUPPLIER_WORDS:
            add(word, WEIGHT_VALUE)
        self._terms = list(terms.items())
        self._pairs = list(pairs.items())

    def terms(self) -> Iterable[tuple[str, int]]:
        return self._terms

    def bigrams(self) -> Iterable[tuple[str, int]]:
        return self._pairs


@dataclass
class TypoResult:
    original: str
    corrected: str
    fixes: list[TypoFix] = field(default_factory=list)


class TypoCorrector:
    def __init__(self, kb: KnowledgeBase) -> None:
        vocabulary = KbVocabulary(kb)
        self._layout = KeyboardLayoutRewriter(vocabulary)
        self._dictionary = SymSpellRewriter(vocabulary)

    def correct(self, text: str) -> TypoResult:
        if not text or not text.strip():
            return TypoResult(text, text)
        fixes: list[TypoFix] = []
        current = text
        for reason, stage in (("layout", self._layout), ("dictionary", self._dictionary)):
            try:
                rewritten = stage.rewrite(current)
            except Exception:  # noqa: BLE001 — стадия не вправе уронить разбор
                rewritten = None
            if not rewritten or rewritten == current:
                continue
            applied, current = _apply(current, rewritten, reason, text)
            fixes += applied
        return TypoResult(text, current, fixes)


def _apply(current: str, rewritten: str, reason: str, original: str) -> tuple[list[TypoFix], str]:
    """Перенести пословные правки стадии в текст пользователя, сохранив регистр и знаки.

    Стадии работают над нормализованным текстом в нижнем регистре; здесь правка применяется
    к исходным словам по месту. Правка, меняющая число слов, не применяется: честно
    сопоставить «две ошибки» с «одна» нельзя. Словоформа справочника («колесиках» →
    «колесиков») — не опечатка: такие правки не применяются и не показываются.
    """
    if reason == "layout":
        return _apply_layout(current, rewritten, original)
    before = tokenize(current)
    after = tokenize(rewritten)
    if len(before) != len(after):
        return [], current
    words = list(_WORD.finditer(current))
    if len(words) != len(before):
        return [], current
    fixes: list[TypoFix] = []
    pieces: list[str] = []
    last = 0
    for match, old, new in zip(words, before, after, strict=True):
        pieces.append(current[last : match.start()])
        token = match.group(0)
        if old != new and not (reason == "dictionary" and is_inflection(old, new)):
            replacement = _keep_case(token, new)
            position = original.find(token)
            fixes.append(
                TypoFix(
                    original=token,
                    corrected=replacement,
                    reason=reason,
                    start=position,
                    end=position + len(token) if position >= 0 else -1,
                )
            )
            pieces.append(replacement)
        else:
            pieces.append(token)
        last = match.end()
    pieces.append(current[last:])
    return fixes, "".join(pieces)


def _apply_layout(current: str, rewritten: str, original: str) -> tuple[list[TypoFix], str]:
    """Раскладка меняет символы вроде «;» на «ж», поэтому слова сопоставляются по пробелам."""
    old_words, new_words = current.split(), rewritten.split()
    if len(old_words) != len(new_words):
        return [], current
    fixes = []
    for old, new in zip(old_words, new_words, strict=True):
        if old.casefold() != new.casefold():
            position = original.find(old)
            fixes.append(
                TypoFix(
                    original=old,
                    corrected=new,
                    reason="layout",
                    start=position,
                    end=position + len(old) if position >= 0 else -1,
                )
            )
    return fixes, " ".join(new_words) if fixes else current


#: Окончания русских прилагательных и существительных — длинные раньше коротких.
_ENDINGS = sorted(
    (
        "ами ями ого его ому ему ыми ими ой ей ий ый ая яя ое ее ые ие ом ем ам ям ах ях ов ев "
        "ую юю ых их ым им а я ы и у ю е о ь"
    ).split(),
    key=len,
    reverse=True,
)


def _split_ending(word: str) -> tuple[str, str]:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)], ending
    return word, ""


def is_inflection(before: str, after: str) -> bool:
    """«колесиках» → «колесиков» — словоформа справочника, а не опечатка.

    Основа одна, окончания разные и оба настоящие. «одеждыы» (окончания «ыы» нет) и
    «шуруповрет» (переставлены буквы в основе) — опечатки: их исправляют и показывают.
    """
    stem_before, end_before = _split_ending(before)
    stem_after, end_after = _split_ending(after)
    if stem_before == stem_after and (end_before or end_after):
        return True
    # «Халаты» → «Халат»: у одной формы окончание нулевое — только у основы на согласную,
    # иначе «одеждыы» → «одежды» выглядело бы словоформой.
    short, long_ = sorted((before, after), key=len)
    return (
        long_[: len(short)] == short
        and long_[len(short) :] in _ENDINGS
        and short[-1:] not in "аеёиоуыэюяьй"
    )


def _keep_case(original: str, word: str) -> str:
    if original.isupper() and len(original) > 1:
        return word.upper()
    if original[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


_CORRECTORS: dict[int, TypoCorrector] = {}


def get_corrector(kb: KnowledgeBase) -> TypoCorrector:
    """Корректор на базу знаний: индекс SymSpell строится один раз на процесс."""
    corrector = _CORRECTORS.get(id(kb))
    if corrector is None:
        corrector = _CORRECTORS[id(kb)] = TypoCorrector(kb)
    return corrector
