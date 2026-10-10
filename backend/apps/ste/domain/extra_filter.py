"""Фильтр дополнительных характеристик: только свойства товара, без оценок и «описаний».

LLM-2 кладёт в ``extra`` всё, чего нет в схеме семейства. Вместе с полезным («Принт: пони»)
туда попадает и шум из текста поставщика: «Описание: афигенный», «Качество: отличное». Такое
в карточку СТЕ не идёт. Фильтр детерминированный, без LLM:

* название из списка общих полей («Описание», «Комментарий», «Отзыв» …) — характеристика
  отбрасывается целиком: это не свойство, а пересказ текста;
* оценочные слова и усилители («афигенный», «классный», «очень», «супер») из значения
  вырезаются; если, кроме них, ничего не осталось — характеристика отбрасывается;
* название само оценочное («Крутизна») — отбрасывается.

Модуль чистый: без Django и ввода-вывода.
"""

from __future__ import annotations

import re

from apps.ste.domain.values import fold_text

#: Общие поля, которые свойством товара не являются (сравнение без регистра и «ё»).
BANNED_NAMES = frozenset(
    fold_text(name)
    for name in (
        "описание",
        "описание товара",
        "комментарий",
        "отзыв",
        "оценка",
        "впечатление",
        "качество",
        "особенности",
        "особенность",
        "преимущества",
        "достоинства",
        "примечание",
        "прочее",
        "другое",
        "дополнительно",
        "дополнительная информация",
        "характеристика",
        "характеристики",
        "свойства",
        "внешний вид",
        "стиль",
        "настроение",
    )
)

#: Основы оценочных слов: значение из одних них — не свойство товара. Основы подобраны так,
#: чтобы не задеть свойства: не «клас» («класс защиты»), не «крут» («крутящий момент»),
#: не «мил» («миллиметр») — такие слова в EVALUATIVE_WORDS целиком.
EVALUATIVE_STEMS = (
    "афиг",
    "офиг",
    "обалден",
    "охрен",
    "потряс",
    "шикарн",
    "роскошн",
    "классн",
    "супер",
    "топов",
    "лучш",
    "отличн",
    "превосход",
    "прекрасн",
    "замечательн",
    "великолепн",
    "идеальн",
    "чудесн",
    "восхитит",
    "изумит",
    "бомбическ",
    "кайфов",
    "прикольн",
    "клёв",
    "клев",
    "зачетн",
    "зачётн",
    "хорошеньк",
    "ужасн",
    "отстойн",
    "красив",
    "симпатичн",
    "стильн",
    "модн",
    "трендов",
    "элегантн",
    "качествен",
    "надежн",
    "надёжн",
    "премиальн",
    "эксклюзивн",
    "уникальн",
    "популярн",
    "любим",
    "незабываем",
    "выгодн",
    "недорог",
    "бюджетн",
)
#: Оценочные слова, которые сравниваются целиком (их основа совпала бы со свойствами).
EVALUATIVE_WORDS = frozenset(
    fold_text(word)
    for word in (
        "крутой",
        "крутая",
        "крутое",
        "крутые",
        "круто",
        "класс",
        "классно",
        "огонь",
        "кайф",
        "топ",
        "норм",
        "нормальный",
        "нормальная",
        "хороший",
        "хорошая",
        "хорошее",
        "хорошие",
        "плохой",
        "плохая",
        "милый",
        "милая",
        "милое",
        "милые",
        "премиум",
        "дешевый",
        "дешёвый",
        "дешевая",
        "дешёвая",
        "отпад",
        "бомба",
        "шик",
        "лучший",
    )
)
#: Служебные слова и усилители, которые сами по себе ничего не значат.
FILLER_WORDS = frozenset(
    fold_text(word)
    for word in (
        "очень",
        "самый",
        "самая",
        "самое",
        "просто",
        "реально",
        "прям",
        "прямо",
        "так",
        "такой",
        "такая",
        "такое",
        "ну",
        "вообще",
        "совсем",
        "весьма",
        "крайне",
        "максимально",
        "и",
        "а",
        "но",
        "же",
        "ли",
        "вот",
        "тут",
        "всё",
        "все",
        "просто-напросто",
    )
)

_WORD = re.compile(r"[0-9a-zа-яё-]+", re.IGNORECASE)


def _is_evaluative(word: str) -> bool:
    folded = fold_text(word)
    return (
        folded in FILLER_WORDS
        or folded in EVALUATIVE_WORDS
        or any(folded.startswith(fold_text(stem)) for stem in EVALUATIVE_STEMS)
    )


def clean_value(value: str) -> str | None:
    """Значение без оценочных слов; ``None`` — кроме оценок, в нём ничего не было.

    «афигенный» → ``None``; «красивый принт пони» → «принт пони»; «синий» → «синий».
    Числа и прочие символы сохраняются: вырезаются только слова-оценки.
    """
    words = _WORD.findall(value)
    if not words:
        return value.strip() or None
    meaningful = [word for word in words if not _is_evaluative(word)]
    if not meaningful:
        return None
    if len(meaningful) == len(words):
        return value.strip()
    return " ".join(meaningful)


def keep_extra(item: dict) -> dict | None:
    """Дополнительная характеристика после фильтра или ``None`` — её не показывать."""
    name = str(item.get("name") or "").strip(" :;.")
    if not name or fold_text(name) in BANNED_NAMES:
        return None
    name_words = _WORD.findall(name)
    if name_words and all(_is_evaluative(word) for word in name_words):
        return None
    value = item.get("value")
    if isinstance(value, str):
        cleaned = clean_value(value)
        if cleaned is None:
            return None
        if cleaned != value.strip():
            return {**item, "value": cleaned}
    elif isinstance(value, list | tuple):
        parts = [clean_value(str(part)) for part in value]
        parts = [part for part in parts if part]
        if not parts:
            return None
        return {**item, "value": parts}
    return dict(item)


def filter_extra(items: list[dict]) -> list[dict]:
    return [kept for item in items if (kept := keep_extra(item)) is not None]


# --- дубли основных характеристик, бренда и модели ---------------------------------------------

#: Названия, под которыми модель кладёт в extra бренд или модель.
BRAND_NAMES = frozenset(
    fold_text(name)
    for name in ("бренд", "производитель", "марка", "торговая марка", "изготовитель", "фирма")
)
MODEL_NAMES = frozenset(fold_text(name) for name in ("модель", "артикул", "серия"))
#: Похожесть названий, при которой это одна характеристика (как в словаре названий).
NAME_MATCH = 0.86
#: Короткие значения («да», «нет», «1») совпадают у разных характеристик случайно.
_TRIVIAL_VALUES = frozenset({"да", "нет", "есть"})
#: Слова названия, которые сами по себе характеристику не определяют.
_GENERIC_NAME_STEMS = frozenset({"тип", "вид", "форма", "налич", "колич", "общий", "основ"})


def _stems(text: str) -> set[str]:
    return {fold_text(word)[:5] for word in _WORD.findall(text) if len(word) >= 3}


def _value_text(value) -> str:
    if isinstance(value, list | tuple):
        return ", ".join(str(part) for part in value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value)


def same_name(extra_name: str, char_name: str) -> bool:
    """Одна ли это характеристика: «Цвет» и «Цвет изделия», «Размер» и «Размер одежды».

    Совпадение целиком, высокая похожесть написания или все слова одного названия есть в
    другом (по основам: «размера» = «размер»).
    """
    import difflib

    a, b = fold_text(extra_name).strip(), fold_text(char_name).strip()
    if not a or not b:
        return False
    if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= NAME_MATCH:
        return True
    sa, sb = _stems(a), _stems(b)
    small = min(sa, sb, key=len)
    if not small or small <= _GENERIC_NAME_STEMS:
        return False  # «Тип», «Вид» — к любой «Тип …» не приклеивать
    return sa <= sb or sb <= sa


def _inside(quote: str | None, other: str | None) -> bool:
    """Цитата extra целиком внутри цитаты основной: то же место текста, та же информация.

    Обратное — не дубль: «Молоток - гвоздодер 560 г» содержит «гвоздодер» основной, но масса
    в нём — другая информация.
    """
    if not quote or not other:
        return False
    a, b = fold_text(quote).strip(), fold_text(other).strip()
    return len(a) >= 3 and a in b


def dedupe_extra(
    items: list[dict],
    chars: dict[str, str],
    filled: dict[str, tuple[str, str | None]],
    brand: str | None,
    model: str | None,
) -> tuple[list[dict], list[tuple[str, dict]], list[tuple[dict, str]]]:
    """Убрать из extra то, что уже есть в основных характеристиках, бренде или модели.

    ``chars`` — ключ → название характеристики семейства; ``filled`` — ключ → (значение,
    цитата) заполненных. Возвращает ``(оставить, перенести в основные, отброшено)``:

    * название как у основной, и она заполнена — дубль, отбрасывается;
    * название как у одной пустой основной — значение переносится туда (там его проверит КТРУ);
      подходят несколько пустых — неясно куда, остаётся в extra;
    * цитата или значение совпадают с заполненной основной — дубль под другим именем;
    * значение равно бренду или модели, или это поле «Бренд»/«Модель» — дубль отдельных полей.
    """
    keep: list[dict] = []
    move: list[tuple[str, dict]] = []
    dropped: list[tuple[dict, str]] = []
    marks = {fold_text(mark) for mark in (brand, model) if mark}
    for item in items:
        name = str(item.get("name") or "")
        value = _value_text(item.get("value"))
        folded_name, folded_value = fold_text(name).strip(), fold_text(value).strip()
        if folded_value and folded_value in marks:
            dropped.append((item, "совпадает с брендом или моделью"))
            continue
        if (folded_name in BRAND_NAMES and brand) or (folded_name in MODEL_NAMES and model):
            dropped.append((item, "бренд и модель — отдельными полями"))
            continue
        same = [key for key, char_name in chars.items() if same_name(name, char_name)]
        if any(key in filled for key in same):
            dropped.append((item, "уже есть в основных характеристиках"))
            continue
        if len(same) == 1:
            move.append((same[0], item))
            continue
        twin = next(
            (
                key
                for key, (filled_value, filled_quote) in filled.items()
                if _inside(item.get("quote"), filled_quote)
                or (
                    folded_value not in _TRIVIAL_VALUES
                    and len(folded_value) >= 3
                    and folded_value == fold_text(filled_value).strip()
                )
            ),
            None,
        )
        if twin is not None:
            dropped.append((item, f"то же значение, что у «{chars.get(twin, twin)}»"))
            continue
        keep.append(item)
    return keep, move, dropped
