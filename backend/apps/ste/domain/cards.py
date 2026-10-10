"""Карточки семейств КТРУ: текст для промпта LLM-1 и для поискового индекса (архитектура §3.8).

Три формы одного и того же семейства:

* :func:`short_card` — одна строка на ~25–40 токенов: весь список семейств помещается в промпт
  LLM-1 (≈ 10 тыс. токенов на охват);
* :func:`full_card` — документ индекса (BM25 + эмбеддинги): всё, по чему поставщик может
  назвать товар — синонимы, описание, «виды», значения и названия характеристик;
* :func:`generic_card` — общая позиция («Инструмент ручной прочий») для отдельного списка
  запасных вариантов в промпте LLM-1.

Синонимы, описание и ключевые слова приходят из LLM-метаданных (§3.7, ``kb.apply_meta``);
пока их нет, карточка строится только из КТРУ. Модуль чистый: ни Django, ни файлов.
"""

from __future__ import annotations

import re

from apps.ste.domain.kb import Family, FamilyChar
from apps.ste.domain.values import fold_text

#: Лимиты короткой карточки: длиннее — список семейств перестаёт помещаться в промпт.
SHORT_KINDS = 10
SHORT_SYNONYMS = 4
SHORT_OKPD2_NAME = 60
SHORT_VALUE = 40
#: «Виды» в короткой карточке — не длиннее стольких символов, даже если их меньше 10.
SHORT_KINDS_CHARS = 200

#: Лимиты полной карточки: длинный документ размывает и BM25 (нормировка длины), и вектор.
FULL_KINDS = 30
FULL_VALUES = 40
FULL_SYNONYMS = 20
FULL_KEYWORDS = 12
FULL_CHARS = 30
FULL_DESCRIPTION = 300

#: Значения, которые ничего не говорят о типе товара: «Да/Нет» есть у половины семейств.
_NOISE_VALUES = frozenset({"да", "нет", "отсутствует", "есть", "имеется"})

#: Характеристики, чьи значения называют разновидность товара, — первыми в «видах».
_KIND_NAME = re.compile(r"^(вид|тип|наименование|по высоте|назначение)")

#: Числовые «значения» вида «33,5» или «44-46» — размеры, не виды.
_NUMERIC_VALUE = re.compile(r"^[\d\s.,/\-–xх×]+$")


def okpd2_group(code: str) -> str:
    """«28.24.11.000» → «28.24.11»: нулевой хвост кода ОКПД2 ничего не добавляет."""
    return re.sub(r"(\.000)+$", "", code.strip())


def _clip(text: str, limit: int) -> str:
    """Обрезать по границе слова — обрубок слова путает и модель, и стеммер."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return (cut or text[:limit]) + "…"


def _useful(value: str) -> bool:
    folded = fold_text(value)
    return bool(folded) and folded not in _NOISE_VALUES and not _NUMERIC_VALUE.match(folded)


def _unique(values: list[str], limit: int) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        value = value.strip()
        if value and _useful(value):
            seen.setdefault(fold_text(value), value)
        if len(seen) >= limit:
            break
    return list(seen.values())


def _kind_first(chars: list[FamilyChar]) -> list[FamilyChar]:
    """«Вид», «Тип», «Наименование изделия» — раньше «Половой признак» и «Класса защиты»."""
    return sorted(chars, key=lambda char: 0 if _KIND_NAME.match(char.key) else 1)


def kinds(family: Family, limit: int = FULL_KINDS) -> list[str]:
    """«Виды» семейства: значения неизменяемых различающих характеристик (``kind=1``).

    Именно они отличают листья друг от друга («Углошлифовальная машина», «Куртка», «От сети»),
    поэтому поставщик чаще всего называет товар через них. «Да/Нет» и размеры отброшены.
    """
    chars = [char for char in family.chars.values() if 1 in char.kinds and char.differentiator]
    values = [value for char in _kind_first(chars) for value in char.text_values()]
    return _unique(values, limit)


def other_values(family: Family, limit: int = FULL_VALUES) -> list[str]:
    """Значения остальных качественных характеристик «вид/тип/назначение…».

    Нужны индексу: у «Одежды санитарной женской» «Халат» — значение ``kind=3``, а не вид, но
    именно по нему поставщик и ищет («халат медицинский»).
    """
    taken = {fold_text(value) for value in kinds(family, limit=10_000)}
    chars = [
        char
        for char in family.chars.values()
        if not (1 in char.kinds and char.differentiator) and _KIND_NAME.match(char.key)
    ]
    values = [
        value for char in chars for value in char.text_values() if fold_text(value) not in taken
    ]
    return _unique(values, limit)


def char_names(family: Family, limit: int = FULL_CHARS) -> list[str]:
    return [char.name for char in family.chars.values()][:limit]


def summary(family: Family, limit: int = FULL_DESCRIPTION) -> str | None:
    """Описание: из LLM-метаданных, иначе описание позиции КТРУ (есть у медицинских изделий)."""
    text = (family.summary or family.description or "").strip().strip('"«»')
    return _clip(text, limit) if text else None


def _okpd2(family: Family, name_limit: int | None) -> str:
    name = family.okpd2_name or ""
    if name_limit is not None:
        name = _clip(name, name_limit)
    return f"{okpd2_group(family.okpd2)} {name}".strip()


def _okpd2_short(family: Family) -> str:
    """Группа ОКПД2 для короткой карточки: без имени группы, если оно повторяет название.

    «Обувь повседневная с верхом из кожи | ОКПД2 15.20.13.110 Обувь повседневная с верхом из
    кожи» — повтор съедает токены промпта и ничего не говорит модели.
    """
    name, group = fold_text(family.name), fold_text(family.okpd2_name or "")
    if not group or group[:25] == name[:25]:
        return "ОКПД2 " + okpd2_group(family.okpd2)
    return "ОКПД2 " + _okpd2(family, SHORT_OKPD2_NAME)


def _short_kinds(family: Family) -> list[str]:
    """«Виды» для LLM-1: значения **каждой** различающей неизменяемой характеристики.

    Первые десять значений подряд показывали бы только одну характеристику: у «Одежды
    специальной для защиты от пониженных температур» это виды одежды, а то, что прямо
    называет семейство («3 (IV климатический пояс)»), в карточку не попадало — и LLM-1
    отдавала «куртку для IV климатического пояса» обычным зимним курткам.
    """
    groups = [
        (char.name, char.text_values())
        for char in family.chars.values()
        # Одно значение на всё семейство ничего не различает — в «виды» не идёт.
        if 1 in char.kinds and char.differentiator and len(char.text_values()) >= 2
    ]
    if len(groups) <= 1:
        result: list[str] = []
        used = 0
        for value in kinds(family, SHORT_KINDS):
            value = _clip(value, SHORT_VALUE)
            if result and used + len(value) + 2 > SHORT_KINDS_CHARS:
                break
            result.append(value)
            used += len(value) + 2
        return result
    per_group = max(2, SHORT_KINDS // len(groups))
    result = []
    for name, values in groups[:4]:
        shown = [_clip(value, SHORT_VALUE) for value in values[:per_group]]
        more = "…" if len(values) > per_group else ""
        result.append(f"{name}: " + ", ".join(shown) + more)
    return result


def short_card(family: Family) -> str:
    """Короткая карточка для промпта LLM-1 (§3.8): id, название, синонимы, ОКПД2, ≤10 видов.

    ``[F399] Шлифмашина | синонимы: болгарка, УШМ | ОКПД2 28.24.11 Инструменты ручные
    электрические | виды: Углошлифовальная машина; Эксцентриковая (орбитальная); …``
    """
    parts = [f"[{family.short_id}] {family.name}"]
    synonyms = _unique(family.synonyms, SHORT_SYNONYMS)
    if synonyms:
        parts.append("синонимы: " + ", ".join(synonyms))
    parts.append(_okpd2_short(family))
    family_kinds = _short_kinds(family)
    if family_kinds:
        parts.append("виды: " + "; ".join(family_kinds))
    return " | ".join(parts)


def generic_card(family: Family) -> str:
    """Общая позиция для промпта LLM-1: id, название, группа ОКПД2 (+ синонимы, если есть).

    Название общей позиции часто совпадает с названием группы ОКПД2 — тогда группа без
    повтора имени, только код.
    """
    parts = [f"[{family.short_id}] {family.name}"]
    synonyms = _unique(family.synonyms, SHORT_SYNONYMS)
    if synonyms:
        parts.append("синонимы: " + ", ".join(synonyms))
    parts.append(_okpd2_short(family))
    return " | ".join(parts)


def full_card(family: Family) -> str:
    """Полная карточка — документ поискового индекса (§3.8, §3.9).

    Порядок строк важен для эмбеддера: в начале то, как товар называют (название, синонимы,
    описание), в конце — названия характеристик, которые лишь подтверждают тип.
    """
    lines = [f"[{family.short_id}] {family.name}"]
    synonyms = _unique(family.synonyms, FULL_SYNONYMS)
    if synonyms:
        lines.append("синонимы: " + ", ".join(synonyms))
    about = summary(family)
    if about:
        lines.append("что это: " + about)
    if family.role == "generic":
        lines.append(f"общая позиция без характеристик, категория: {family.category}")
    else:
        lines.append(f"категория: {family.category}")
    lines.append("ОКПД2: " + _okpd2(family, None))
    family_kinds = kinds(family, FULL_KINDS)
    if family_kinds:
        lines.append("виды: " + "; ".join(family_kinds))
    values = other_values(family, FULL_VALUES)
    if values:
        lines.append("варианты: " + "; ".join(values))
    names = char_names(family, FULL_CHARS)
    if names:
        lines.append("характеристики: " + ", ".join(names))
    keywords = _unique(family.keywords, FULL_KEYWORDS)
    if keywords:
        lines.append("ключевые слова: " + ", ".join(keywords))
    return "\n".join(lines)
