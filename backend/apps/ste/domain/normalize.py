"""Нормализация значения характеристики: текст пользователя → значение КТРУ (архитектура §4.4).

LLM-2 (или правила, или ответ пользователя) отдаёт значение «как в тексте»: строку из enum
семейства, число с единицей «как написано». Здесь код решает то, что нельзя доверить модели:

* качественное значение сопоставляется с допустимыми текстами КТРУ — точно, а при опечатке
  или другой словоформе — нечётко, но консервативно: неоднозначное сопоставление хуже, чем
  никакого, потому что уверенное значение **исключает** листья;
* число переводится в единицу каждого допустимого значения КТРУ отдельно (у значений одной
  характеристики могут быть разные ОКЕИ) и проверяется на попадание с учётом строгих границ;
* размерность никогда не пересекается: «5 кг» для длины — не число в миллиметрах, а ошибка;
* уверенность (§4.4) решает, может ли значение исключать листья при сужении (§4.5).

Модуль чистый: без Django и ввода-вывода.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher

from apps.ste.domain import units as U
from apps.ste.domain.kb import CharSpec, FamilyChar
from apps.ste.domain.model import Confidence, ExtractedValue, NormalizedValue, Source, Status
from apps.ste.domain.values import AllowedValue, fold_text, format_number

Spec = FamilyChar | CharSpec

#: Порог нечёткого совпадения всей строки (difflib): ловит опечатки в одном-двух символах.
FUZZY_RATIO = 0.85
#: Общая основа слов: столько первых букв совпало — это одно слово в другой форме.
STEM_PREFIX = 5
#: Какая доля значимых слов значения КТРУ должна найтись у пользователя.
MIN_COVERAGE = 0.6

#: Служебные слова не различают значения: «От сети» и «сетевой» роднит «сеть», а не «от».
_STOP_WORDS = frozenset(
    {"от", "с", "со", "из", "в", "во", "на", "для", "и", "или", "по", "без", "до", "не", "к", "а"}
)
#: Окончания прилагательных и существительных (после свёртки ё → е).
_ENDINGS = frozenset(
    {"", "а", "я", "ы", "и", "у", "ю", "е", "о", "ь", "ой", "ей", "ий", "ый", "ая", "яя", "ое",
     "ее", "ые", "ие", "ую", "юю", "ого", "его", "ому", "ему", "ым", "им", "ых", "их", "ам", "ям",
     "ами", "ями", "ах", "ях", "ом", "ем", "ов", "ев"}
)  # fmt: skip
#: Отрицание меняет смысл на противоположный: «не металлический» ≠ «Металлический»,
#: «без капюшона» ≠ «С капюшоном». Нечёткое сопоставление через отрицание запрещено.
_NEGATIONS = frozenset({"не", "без", "нет", "ни"})
_TOKEN = re.compile(r"[0-9a-zа-яё]+")
_SPACES = "[ \u00a0\u202f]"
_NUMBER = re.compile(rf"(?:\d{{1,3}}(?:{_SPACES}\d{{3}})+|\d+)(?:[.,]\d+)?")
_ITEM_SEPARATORS = re.compile(r"\s*[;,]\s*")


# --- числа ---------------------------------------------------------------------------------


def parse_number(text: str | float | None) -> float | None:
    """Число, как его пишут: «0,84», «1 200», «2.03000», «125».

    Только неотрицательные десятичные числа целиком (в КТРУ по охвату нет отрицательных
    значений): дроби «1/2», диапазоны и числа с хвостом дают ``None`` — угадывать здесь
    опаснее, чем признать, что числа нет.
    """
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, int | float):
        value = float(text)
        return value if math.isfinite(value) and value >= 0 else None
    raw = str(text).strip()
    if not _NUMBER.fullmatch(raw):
        return None
    return float(re.sub(_SPACES, "", raw).replace(",", "."))


def split_number_unit(text: str | None) -> tuple[float | None, str | None]:
    """«125мм» → (125, «мм»); «1 200 Вт» → (1200, «Вт»); «12» → (12, None).

    Нужна, когда модель вернула число вместе с единицей одной строкой. Хвост, похожий на
    продолжение числа («1/2», «1.2.3»), означает, что числа нет.
    """
    if not text:
        return None, None
    raw = str(text).strip()
    match = _NUMBER.match(raw)
    if match is None:
        return None, None
    rest = raw[match.end() :].strip()
    if rest and (rest[0] in "/.,-" or rest[0].isdigit()):
        return None, None
    return parse_number(match.group(0)), (rest or None)


def representative(value: AllowedValue) -> float:
    """Число внутри допустимого значения: ответ кнопкой «≥125 <130» сужает как 127,5.

    Для открытой строгой границы берётся точка чуть внутри, чтобы «<300» не совпало с 300.
    """
    if value.kind == "concrete":
        return float(value.concrete or 0.0)
    low, high = value.minimum, value.maximum
    if low is not None and high is not None:
        return low if low == high else (low + high) / 2
    if low is not None:
        return low if value.min_inclusive else low + max(abs(low) * 1e-6, 1e-6)
    if high is not None:
        return high if value.max_inclusive else high - max(abs(high) * 1e-6, 1e-6)
    return 0.0


def _clean(value: float) -> float:
    """Убрать шум двоичной арифметики: 1,1 кВт → 1100, а не 1100,0000000000002.

    Иначе число ровно на нестрогой границе («≥1100») выпало бы из интервала.
    """
    return float(f"{value:.12g}")


def convert_value(value: float, source: U.Unit | None, target: U.Unit | None) -> float | None:
    """Перевод с учётом безразмерных значений: без единицы с любой стороны — число как есть.

    ``None`` — размерности разные (граммы в миллиметры не переводятся).
    """
    if source is None or target is None or source.code == target.code:
        return value
    converted = U.convert(value, source, target)
    return None if converted is None else _clean(converted)


def fits_number(
    allowed: Iterable[AllowedValue], number: float, unit: U.Unit | None
) -> list[AllowedValue]:
    """Допустимые числовые значения, в которые попадает ``number`` (в единице ``unit``).

    Каждое значение проверяется в своей единице ОКЕИ; другой размерности — не подходит.
    """
    hits = []
    for value in allowed:
        if not value.is_numeric:
            continue
        converted = convert_value(number, unit, value.unit or unit)
        if converted is not None and value.contains_number(converted):
            hits.append(value)
    return hits


def _tightness(value: AllowedValue) -> tuple[int, float]:
    """Порядок «самое узкое первым»: при пересечении «≥1» и «≥2» для 2 штук важнее «≥2»."""
    if value.kind == "concrete":
        return 0, 0.0
    if value.minimum is not None and value.maximum is not None:
        return 1, value.maximum - value.minimum
    if value.minimum is not None:
        return 2, -value.minimum
    if value.maximum is not None:
        return 2, value.maximum
    return 3, 0.0


# --- тексты --------------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    return [word for word in _TOKEN.findall(fold_text(text)) if word not in _STOP_WORDS]


def _negations(text: str) -> set[str]:
    return {word for word in _TOKEN.findall(fold_text(text)) if word in _NEGATIONS}


def _same_polarity(folded: str, negations: set[str], option: str) -> bool:
    """Можно ли нечётко сопоставлять текст с вариантом, не перевернув смысл.

    Отрицание у пользователя, которого нет в варианте, — нельзя («не металлический»).
    Отрицание варианта, которого нет у пользователя отдельным словом, допустимо, только
    если оно приклеилось опечаткой: «поворотныебез ускоренного…».
    """
    option_negations = _negations(option)
    if negations - option_negations:
        return False
    return all(word in folded for word in option_negations - negations)


def _same_stem(left: str, right: str) -> bool:
    """Одно слово в разных формах: «мужская»/«Мужской», «курток»/«Куртка», «пружиный»/…"""
    if left == right:
        return True
    if left.isdigit() or right.isdigit() or min(len(left), len(right)) < 3:
        return False
    prefix = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        prefix += 1
    if prefix >= STEM_PREFIX:
        return True
    # Короткие слова: общая основа из 4 букв и окончания не длиннее двух.
    if prefix >= 4 and len(left) - prefix <= 2 and len(right) - prefix <= 2:
        return True
    # Основа из 3 букв («син-ий»/«син-его», «кож-а»/«кож-и») — только если оба хвоста —
    # падежные окончания; иначе «сети» совпало бы с «сетка».
    return prefix >= 3 and left[prefix:] in _ENDINGS and right[prefix:] in _ENDINGS


def map_text(value: str, options: Sequence[str]) -> str | None:
    """Значение КТРУ из ``options``, которое имел в виду пользователь, или ``None``.

    Порядок: точное совпадение (регистр, ё, кавычки) → опечатка во всей строке (difflib) →
    совпадение по основам слов. Если два варианта одинаково хороши («металлический каркас
    с деревянной столешницей» для «Тип каркаса»), ответа нет: угаданное значение исключило
    бы правильный лист. Через отрицание («не металлический») нечётко не сопоставляем.
    """
    folded = fold_text(value or "")
    if not folded or not options:
        return None
    for option in options:
        if fold_text(option) == folded:
            return option

    negations = _negations(value)
    eligible = [
        index for index, option in enumerate(options) if _same_polarity(folded, negations, option)
    ]
    if not eligible:
        return None
    ratios = sorted(
        ((SequenceMatcher(None, folded, fold_text(options[i])).ratio(), i) for i in eligible),
        reverse=True,
    )
    best_ratio, best_index = ratios[0]
    second_ratio = ratios[1][0] if len(ratios) > 1 else 0.0
    if best_ratio >= FUZZY_RATIO and best_ratio > second_ratio:
        return options[best_index]

    user_words = _tokens(value)
    if not user_words:
        return None
    scored: list[tuple[float, int, int]] = []
    for index in eligible:
        words = _tokens(options[index])
        if not words:
            continue
        hits = [word for word in words if any(_same_stem(item, word) for item in user_words)]
        coverage = len(hits) / len(words)
        # Совпадение одних чисел («1» в «1 3/16») ничего не говорит о смысле значения.
        if coverage >= MIN_COVERAGE and any(not word.isdigit() for word in hits):
            scored.append((coverage, len(hits), index))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
        return None
    return options[scored[0][2]]


def split_items(value: str | float | list | None, multi: bool) -> list[str]:
    """Значение как список строк. Для выбора нескольких строку режем по «,» и «;»."""
    if value is None:
        return []
    if isinstance(value, list | tuple):
        items = [_as_text(item) for item in value]
    else:
        text = _as_text(value)
        items = _ITEM_SEPARATORS.split(text) if multi else [text]
    return [item for item in (item.strip() for item in items) if item]


def _as_text(value: object) -> str:
    if isinstance(value, float | int) and not isinstance(value, bool):
        return format_number(float(value))
    return str(value)


# --- характеристика ------------------------------------------------------------------------


def spec_kinds(spec: Spec) -> tuple[int, ...]:
    """Виды характеристики: у листа один, у семейства — объединение по листьям."""
    kinds = getattr(spec, "kinds", None)
    return tuple(kinds) if kinds else (spec.kind,)


def is_multi(spec: Spec) -> bool:
    """Выбор нескольких значений (kind=3) — только если других видов у характеристики нет."""
    return spec_kinds(spec) == (3,)


def has_content(nv: NormalizedValue) -> bool:
    """Есть ли что сравнивать с КТРУ: число или хотя бы текст пользователя."""
    if nv.value_type == "number":
        return nv.number is not None
    return bool(nv.texts) or bool(nv.user_text.strip())


def is_interval_choice(nv: NormalizedValue) -> bool:
    """Значение выбрано кнопкой-интервалом («≥125 <130 Миллиметр»), а не написано числом.

    Число такого значения — служебная точка внутри интервала (:func:`representative`),
    показывать пользователю нужно сам интервал.
    """
    return nv.value_type == "number" and bool(nv.user_text) and nv.user_text in nv.matched


def display_value(nv: NormalizedValue) -> str:
    """Короткая запись значения для воронки и карточки: «125 Миллиметр», «Куртка, Брюки»."""
    if nv.value_type == "number":
        if nv.number is None or is_interval_choice(nv):
            return nv.user_text
        if nv.unit_ok and nv.number_in_ktru_unit is not None:
            return f"{format_number(nv.number_in_ktru_unit)} {nv.ktru_unit or ''}".strip()
        return f"{format_number(nv.number)} {nv.user_unit or ''}".strip()
    return ", ".join(nv.texts) if nv.texts else nv.user_text


def normalize_value(spec: Spec, ev: ExtractedValue) -> NormalizedValue:
    """Привести значение из текста к КТРУ и оценить уверенность (архитектура §4.4).

    ``confident`` — статус «указано», цитата найдена и значение однозначно легло на КТРУ:
    текст сопоставлен с допустимым значением, а у числа единица распознана и той же
    размерности (или единицы нет, но число попало в допустимое значение). ``confirmed``
    (ответ кнопкой, правка) сохраняется как есть. Всё остальное — ``weak``: такое значение
    не исключает листья, а только штрафует их.
    """
    if spec.is_numeric:
        nv, mapped = _normalize_number(spec, ev)
    else:
        nv, mapped = _normalize_text(spec, ev)

    if ev.confidence == Confidence.CONFIRMED:
        nv.confidence = Confidence.CONFIRMED
    elif ev.status == Status.SPECIFIED and ev.quote_found and mapped:
        nv.confidence = Confidence.CONFIDENT
    else:
        nv.confidence = Confidence.WEAK
    return nv


def _user_text(ev: ExtractedValue, fallback: str) -> str:
    """Как написал пользователь: найденная цитата, иначе само значение."""
    if ev.quote and ev.quote_found and ev.quote.strip():
        return ev.quote.strip()
    return fallback


def _normalize_text(spec: Spec, ev: ExtractedValue) -> tuple[NormalizedValue, bool]:
    multi = is_multi(spec)
    options = spec.text_values()
    items = split_items(ev.value, multi=False)
    if (
        multi
        and len(items) == 1
        and not any(fold_text(option) == fold_text(items[0]) for option in options)
    ):
        # Список строкой: «полка для клавиатуры, замок». Целиком — только точное значение
        # КТРУ (в нём самом может быть запятая); иначе нечёткое сопоставление «съело» бы
        # часть списка и молча потеряло второе значение.
        items = split_items(items[0], multi=True)

    texts: list[str] = []
    unmapped = 0
    for item in items:
        mapped = map_text(item, options)
        if mapped is None:
            unmapped += 1
        elif mapped not in texts:
            texts.append(mapped)

    # «Не указано» без значения — пустое значение, даже если модель приложила цитату.
    absent = ev.status == Status.ABSENT and not items
    nv = NormalizedValue(
        char_key=spec.key,
        char_name=spec.name,
        user_text="" if absent else _user_text(ev, "; ".join(items)),
        value_type="multi" if multi else "text",
        texts=texts,
        matched=list(texts),
        source=ev.source,
        quote=ev.quote,
    )
    return nv, bool(texts) and unmapped == 0


def _number_and_unit(ev: ExtractedValue) -> tuple[float | None, str | None, object]:
    """Число и написание единицы из ответа модели: число отдельно или строкой «125 мм»."""
    raw = ev.value
    if isinstance(raw, list | tuple):
        # kind=3 у числовой (размеры обуви списком): сужаем по первому числу.
        raw = next(
            (item for item in raw if parse_number(item) is not None), raw[0] if raw else None
        )
    unit_text = (ev.unit or "").strip() or None
    number = parse_number(raw)
    if number is None and isinstance(raw, str):
        number, tail = split_number_unit(raw)
        unit_text = unit_text or tail
    return number, unit_text, raw


def _normalize_number(spec: Spec, ev: ExtractedValue) -> tuple[NormalizedValue, bool]:
    """Числовое значение и признак «легло на КТРУ однозначно» для расчёта уверенности."""
    number, unit_text, raw = _number_and_unit(ev)
    spec_unit = spec.unit
    user_unit = U.parse_unit(unit_text) if unit_text else None
    written = " ".join(
        part for part in (_as_text(raw) if raw is not None else "", unit_text) if part
    )
    nv = NormalizedValue(
        char_key=spec.key,
        char_name=spec.name,
        user_text=_user_text(ev, written),
        value_type="number",
        number=number,
        user_unit=user_unit.name if user_unit else unit_text,
        ktru_unit=spec_unit.name if spec_unit else None,
        source=ev.source,
        quote=ev.quote,
    )
    if unit_text is not None and (
        user_unit is None or (spec_unit is not None and not U.same_dimension(user_unit, spec_unit))
    ):
        # Единица не распознана или другой размерности: переводить не во что.
        nv.unit_ok = False
        return nv, False
    if number is None:
        return nv, False

    source = user_unit or spec_unit
    nv.number_in_ktru_unit = convert_value(number, source, spec_unit) if spec_unit else number
    hits = fits_number(spec.allowed, number, source)
    nv.matched = [value.describe() for value in sorted(hits, key=_tightness)]
    # Единица проверена, только если у характеристики своя единица есть; иначе (единицы нет
    # или характеристика безразмерная) значение подтверждает лишь попадание в КТРУ.
    unit_checked = user_unit is not None and spec_unit is not None
    return nv, unit_checked or bool(hits)


# --- совместимость с листом (архитектура §4.5) -------------------------------------------------


def compatible(spec: CharSpec | None, nv: NormalizedValue) -> bool | None:
    """Совместимо ли значение с характеристикой листа.

    ``None`` — у листа такой характеристики нет (нейтрально). Значение без содержимого
    совместимо со всем. Значение, которое заведомо не ложится на КТРУ (единица другой
    размерности, текст не из справочника), несовместимо ни с одним листом: при уверенном
    значении это и есть конфликт, который нужно объяснить.
    """
    if spec is None:
        return None
    if not spec.allowed or not has_content(nv):
        return True
    if nv.value_type == "number":
        if not nv.unit_ok:
            return False
        number = nv.number_in_ktru_unit if nv.number_in_ktru_unit is not None else nv.number
        unit = U.parse_unit(nv.ktru_unit) if nv.ktru_unit else None
        if any(value.is_numeric for value in spec.allowed):
            return bool(fits_number(spec.allowed, number, unit))
        # Числовое значение против текстового списка (редкость): сравниваем запись числа.
        return any(value.matches_text(format_number(number)) for value in spec.allowed)
    if not nv.texts:
        return False
    allowed = {fold_text(text) for text in spec.text_values()}
    return all(fold_text(text) in allowed for text in nv.texts)


# --- значение из варианта ответа ---------------------------------------------------------------


def value_from_allowed(
    spec: Spec, value: AllowedValue, source: Source = Source.ANSWER
) -> NormalizedValue:
    """Допустимое значение КТРУ как значение пользователя (ответ кнопкой, дозаполнение).

    Интервал сужает как своё внутреннее число (:func:`representative`), поэтому ответ
    кнопкой проходит через ту же проверку совместимости, что и число из текста.
    """
    if not value.is_numeric:
        text = value.text or ""
        return NormalizedValue(
            char_key=spec.key,
            char_name=spec.name,
            user_text=text,
            value_type="multi" if is_multi(spec) else "text",
            texts=[text],
            matched=[text],
            confidence=Confidence.CONFIRMED,
            source=source,
            quote=text if source != Source.KTRU else None,
        )
    number = representative(value)
    own_unit = value.unit
    spec_unit = spec.unit or own_unit
    in_spec_unit = convert_value(number, own_unit, spec_unit)
    description = value.describe()
    return NormalizedValue(
        char_key=spec.key,
        char_name=spec.name,
        user_text=description,
        value_type="number",
        number=number,
        user_unit=own_unit.name if own_unit else None,
        ktru_unit=spec_unit.name if spec_unit else None,
        number_in_ktru_unit=in_spec_unit if in_spec_unit is not None else number,
        matched=[description],
        confidence=Confidence.CONFIRMED,
        source=source,
        quote=description if source != Source.KTRU else None,
    )


def option_value(spec: Spec, option: str) -> NormalizedValue | None:
    """Ответ кнопкой: ``option`` — текст КТРУ или описание интервала из вопроса.

    ``None`` — такого варианта у характеристики нет (тогда ответ разбирают как свободный текст).
    """
    for value in spec.allowed:
        if value.is_numeric and value.describe() == option:
            return value_from_allowed(spec, value)
        if not value.is_numeric and value.matches_text(option):
            return value_from_allowed(spec, value)
    return None
