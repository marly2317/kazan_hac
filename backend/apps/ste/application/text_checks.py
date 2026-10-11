"""Проверки описания целиком — то, что не видно по одной характеристике (модуль ошибок).

Без LLM и детерминированно, поэтому работают одинаково в любом режиме:

* :func:`negative_value_signals` — «высота -750 мм», «размер -52»: величина не бывает
  отрицательной, и молча отбросить минус (подобрать код как для 750) нельзя;
* :func:`named_families` / :func:`multiple_products_signal` — «стол и стул в одной позиции»:
  в описании названы два разных товара, а СТЕ — это один товар;
* :func:`contradiction_signals` — «диван прямой и угловой одновременно»: у характеристики с
  одним значением в тексте названы два допустимых значения.

Слова сравниваются по полным основам Snowball (как в поиске), а не по первым буквам: иначе
«прямой» совпал бы с «прямоугольный».
"""

from __future__ import annotations

import re

from apps.ste.application.retrieval import _stem, fold
from apps.ste.domain.kb import Family, KnowledgeBase
from apps.ste.domain.model import Severity, Signal, SignalType

_WORD = re.compile(r"[а-яa-z]+")
#: Служебные слова названий: «для», «из», «на» не отличают один товар от другого.
_STOP = frozenset({"для", "из", "на", "с", "со", "и", "в", "во", "по", "от", "без", "под", "при"})
#: «-750 мм», «размер -52»: минус перед числом, а не тире диапазона «1,5–13».
_NEGATIVE = re.compile(r"(?:(?<=\s)|^)[-−–](\d+(?:[.,]\d+)?)\s*([а-яa-z/%.]+)?", re.IGNORECASE)
#: Признаки, что несколько товаров описаны как один.
_TOGETHER = re.compile(
    r"\bи\b|одн(?:им|ой|ом)\s+(?:товар|позиц|издели|лот)|вместе|в\s+комплекте\s+с|\+",
    re.IGNORECASE,
)


def stems(text: str) -> set[str]:
    return {_stem(word) for word in _WORD.findall(fold(text)) if word not in _STOP}


# --- отрицательные значения ---------------------------------------------------------------------


def negative_value_signals(text: str) -> list[Signal]:
    out = []
    for match in _NEGATIVE.finditer(text):
        written = match.group(0).strip()
        out.append(
            Signal(
                type=SignalType.NEGATIVE_VALUE,
                severity=Severity.ERROR,
                message=f"Отрицательное значение «{written}»: размеры, масса и другие величины "
                "товара не бывают меньше нуля.",
                suggestion="Проверьте значение — возможно, имелось в виду "
                f"«{written[1:].strip()}».",
                user_value=written,
                quote=written,
            )
        )
    return out


# --- несколько товаров в одной позиции ------------------------------------------------------


def _named(family: Family, query: set[str]) -> bool:
    """Тип назван в тексте: все значимые слова названия или одного из синонимов есть в запросе."""
    for phrase in (family.name, *family.synonyms):
        words = stems(phrase)
        if words and words <= query:
            return True
    return False


def named_families(text: str, kb: KnowledgeBase, family_ids: list[str]) -> list[Family]:
    """Разные товары, прямо названные в тексте, из кандидатов поиска.

    «Брюки» и «Брюки спортивные» — один товар (название одного входит в другое): остаётся
    более точный. Разные товары — с разным главным словом («Стол …» и «Стул …»).
    """
    if not _TOGETHER.search(text):
        return []
    query = stems(text)
    named = [
        kb.families[fid]
        for fid in dict.fromkeys(family_ids)
        if fid in kb.families
        and kb.families[fid].role == "family"
        and _named(kb.families[fid], query)
    ]
    named_stems = {family.id: stems(family.name) for family in named}
    distinct = [
        family
        for family in named
        if not any(
            other.id != family.id and named_stems[family.id] < named_stems[other.id]
            for other in named
        )
    ]
    heads: dict[str, Family] = {}
    for family in distinct:
        words = _WORD.findall(fold(family.name))
        head = _stem(words[0]) if words else family.id
        heads.setdefault(head, family)
    return list(heads.values()) if len(heads) > 1 else []


def multiple_products_signal(families: list[Family]) -> Signal:
    names = ", ".join(f"«{family.name}»" for family in families)
    return Signal(
        type=SignalType.MULTIPLE_PRODUCTS,
        severity=Severity.ERROR,
        message=f"В одной позиции описано несколько товаров: {names}. "
        "СТЕ — это один товар, у каждого свой код КТРУ.",
        suggestion="Выберите, какой товар оформляем сейчас; остальные опишите "
        "отдельными позициями.",
    )


# --- противоречия внутри текста ----------------------------------------------------------


def _short_value_found(value: str, text: str) -> bool:
    """Короткие значения («A», «B») — только отдельным словом и в том же написании."""
    return re.search(rf"(?<![\w]){re.escape(value)}(?![\w])", text) is not None


def _named_by_head(words: set[str], query: set[str]) -> bool:
    """«трикотаж» называет «Трикотажное полотно»: длинное отличительное слово значения есть
    в тексте (основы совпадают началом). Короткие («кожа») так не считаются — иначе «кожа»
    назвала бы и «Кожа натуральная», и «Кожа искусственная»."""
    for word in words:
        for seen in query:
            short, long_ = sorted((word, seen), key=len)
            if len(short) >= 7 and long_.startswith(short):
                return True
    return False


def contradiction_signals(text: str, family: Family) -> list[Signal]:
    """У характеристики с одним значением в тексте названы два разных допустимых значения."""
    query = stems(text)
    out = []
    for char in family.chars.values():
        if char.is_numeric or 3 in char.kinds:
            continue  # числа проверяет сужение; список (kind=3) допускает несколько значений
        found: list[tuple[str, set[str]]] = []
        for value in char.text_values():
            words = stems(value)
            if len(value) <= 2:
                if value.strip() and _short_value_found(value.strip(), text):
                    found.append((value, {value}))
            elif words and (words <= query or _named_by_head(words, query)):
                found.append((value, words))
        # «Кожа» и «Кожа искусственная» — не противоречие: остаётся более точное значение.
        distinct = [value for value, words in found if not any(words < other for _, other in found)]
        distinct = list(dict.fromkeys(distinct))
        if len(distinct) > 1:
            listed = " и ".join(f"«{value}»" for value in distinct)
            out.append(
                Signal(
                    type=SignalType.CONTRADICTION,
                    severity=Severity.ERROR,
                    message=f"«{char.name}»: в описании одновременно {listed}, а значение может "
                    "быть только одно.",
                    suggestion="Оставьте одно значение.",
                    char=char.name,
                    char_key=char.key,
                    allowed=distinct,
                    family_id=family.id,
                )
            )
    return out


# --- величины, которых у товара нет, и неправдоподобные количества ------------------------------

_NUMBER_WITH_UNIT = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*([а-яa-z][а-яa-z./²³]*)", re.I)
#: Количество (шт) больше этого у мебели, одежды, инструмента не встречается: «999 полок».
IMPLAUSIBLE_COUNT = 100


def unit_signals(text: str, family: Family) -> list[Signal]:
    """«мощность двигателя 2 кВт» у спортивного костюма: величины такой размерности нет ни у
    одной характеристики товара — значит, она к нему не относится. «999 открытых полок» —
    формально «≥3», но неправдоподобно."""
    from apps.ste.domain import units as U

    dimensions = {char.unit.dimension for char in family.chars.values() if char.unit is not None}
    out: list[Signal] = []
    seen: set[str] = set()
    for match in _NUMBER_WITH_UNIT.finditer(text):
        unit = U.parse_unit(match.group(2))
        if unit is None or unit.dimension in dimensions or match.group(0) in seen:
            continue
        seen.add(match.group(0))
        out.append(
            Signal(
                type=SignalType.IRRELEVANT,
                severity=Severity.WARNING,
                message=f"«{match.group(0).strip()}» — у «{family.name}» нет характеристик, "
                f"которые измеряются в «{unit.name}»: значение, похоже, не относится к товару.",
                suggestion="Уберите его из описания или проверьте, тот ли это товар.",
                user_value=match.group(0).strip(),
                quote=match.group(0).strip(),
                family_id=family.id,
            )
        )
    count_chars = [
        char
        for char in family.chars.values()
        if char.is_numeric and char.unit is not None and char.unit.name == "Штука"
    ]
    if count_chars:
        for match in re.finditer(r"(?<![\w.,-])(\d{3,})\s+([а-я]+)", text, re.I):
            if int(match.group(1)) > IMPLAUSIBLE_COUNT:
                out.append(
                    Signal(
                        type=SignalType.IRRELEVANT,
                        severity=Severity.WARNING,
                        message=f"«{match.group(0)}» — неправдоподобное количество для "
                        f"«{family.name}».",
                        suggestion="Проверьте число — возможно, опечатка.",
                        user_value=match.group(0),
                        quote=match.group(0),
                        family_id=family.id,
                    )
                )
    return out
