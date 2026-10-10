"""Шаги пайплайна, которые делает LLM (архитектура §4.1, §4.3, §4.8).

* :func:`select_family` — LLM-1: семейство КТРУ по тексту (кандидаты со score);
* :func:`extract_values` — LLM-2: текст по характеристикам семейства + extra + бренд/модель;
* :func:`extract_one` — мини-LLM-2: одна характеристика из свободного ответа на вопрос.

Модель здесь — источник гипотез, а не истины. Всё, что она вернула, проверяется кодом:
id — только из разрешённых, значения приводятся к допустимым КТРУ, цитата ищется в тексте,
бренд и extra без опоры на текст отбрасываются. Мелкие отклонения формата (``"c1"`` вместо
``"c01"``, ``"0,9"`` вместо 0.9, формат §3.6 вместо списка) исправляются до проверки схемы:
повторный запрос из-за них — это потерянная секунда и запрос из дневного лимита.

**Отличия от §3.6, сознательные:**

* ``characteristics`` — список только упомянутых в тексте характеристик, а не объект со всеми
  ``cNN``: ответ короче в разы (27 характеристик × «не указано» — это ~700 токенов и секунды
  генерации), а неупомянутые всё равно дозаполняются здесь статусом «не указано»;
* у качественных ``value`` — строка, а не ``enum``: значение, которого в КТРУ нет («пластик» для
  материала столешницы), — главный сигнал ошибки (VALUE_NOT_ALLOWED), и ``enum`` заставил бы
  модель подменить его ближайшим допустимым или потерять. Допустимые значения перечислены в
  промпте, а приведение к ним делает код (точное совпадение → difflib ≥ 0,85 → вхождение).
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from apps.ste.application import prompts as P
from apps.ste.domain import units as U
from apps.ste.domain.kb import Family, FamilyChar
from apps.ste.domain.kb import char_key as make_char_key
from apps.ste.domain.model import Confidence, ExtractedValue, Source, Status
from apps.ste.domain.values import fold_text, format_number

if TYPE_CHECKING:
    from apps.ste.infrastructure.llm_client import LlmResponse

#: Цитата модели засчитывается при нечётком совпадении с фрагментом текста не ниже этого.
QUOTE_FUZZY = 0.9
#: Качественное значение модели приводится к допустимому при сходстве не ниже этого.
ENUM_FUZZY = 0.85
#: Бренд/модель/extra засчитываются, если написаны в тексте хотя бы так похоже (латиница ↔
#: кириллица: «Makita» — «макита»).
MENTION_FUZZY = 0.8
#: Короче этого нечёткое совпадение не ищем — только точное вхождение.
FUZZY_MIN_LENGTH = 5
MAX_CANDIDATES = 3

SELECT_MAX_TOKENS = 600
EXTRACT_MAX_TOKENS = 1500
ONE_MAX_TOKENS = 300


class JsonLlm(Protocol):
    """Что шагам нужно от клиента: :class:`~apps.ste.infrastructure.llm_client.LlmClient`."""

    def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: dict,
        name: str,
        max_tokens: int = ...,
        temperature: float = ...,
        prepare: Any = ...,
    ) -> LlmResponse: ...


@dataclass
class SelectionResult:
    #: (short_id, score 0..1, reason), по убыванию score, не больше трёх.
    candidates: list[tuple[str, float, str]]
    not_found: bool
    not_found_reason: str | None = None
    response: LlmResponse | None = None


@dataclass
class ExtractionResult:
    #: Все характеристики семейства; не упомянутые — со статусом «не указано».
    values: dict[str, ExtractedValue]
    #: Существенное из текста, чего нет в схеме: ``{"name", "value", "unit", "quote"}``.
    extra: list[dict]
    brand: str | None = None
    model: str | None = None
    response: LlmResponse | None = None
    #: Вторые и следующие значения одной характеристики — для сигнала DUPLICATE_VALUE.
    duplicates: dict[str, list[ExtractedValue]] = field(default_factory=dict)


# --- общие мелочи --------------------------------------------------------------------------

_NULLS = {"", "null", "none", "nil", "n/a", "нет данных", "-", "—"}


def _opt_str(value: Any) -> str | None:
    """Строка или ``None``: модели пишут «null» строкой и числа там, где ждём текст."""
    if value is None or isinstance(value, dict | list):
        return None
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, int | float):
        return format_number(float(value)) if math.isfinite(value) else None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return None if text.casefold() in _NULLS else text


def _clean_value(value: Any) -> str | float | list[str] | None:
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, list):
        items = [_opt_str(item) for item in value]
        return [item for item in items if item]
    return _opt_str(value)


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    folded = str(value).strip().casefold()
    if folded in {"true", "да", "yes", "1"}:
        return True
    if folded in {"false", "нет", "no", "0", ""}:
        return False
    return default


# --- цитаты --------------------------------------------------------------------------------


def normalize_for_quote(text: str) -> str:
    """Регистр, ё→е, пробелы — так сравниваются цитата и текст."""
    return re.sub(r"\s+", " ", text.casefold().replace("ё", "е")).strip()


def _clean_quote(quote: str | None) -> str | None:
    if not quote:
        return None
    cleaned = quote.strip().strip("«»\"'“”„`").strip()
    return cleaned or None


def _fuzzy_found(needle: str, haystack: str, threshold: float) -> bool:
    """Есть ли в ``haystack`` фрагмент, похожий на ``needle`` не меньше ``threshold``.

    Окна начинаются на границах слов (цитата — это слова текста) и имеют длину около длины
    цитаты: при меньшей/большей длине отношение SequenceMatcher не дотянет до порога.
    """
    if not needle or not haystack:
        return False
    if needle in haystack:
        return True
    size = len(needle)
    if size < FUZZY_MIN_LENGTH:
        # Короткое сравнивается только точно: «бош» на 80 % похоже на «бо» из «болгарки».
        return False
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(needle)
    starts = [0] + [index for index in range(1, len(haystack)) if not haystack[index - 1].isalnum()]
    low = max(1, math.floor(size * threshold / (2 - threshold)))
    high = math.ceil(size * (2 - threshold) / threshold)
    for start in starts:
        for width in range(low, high + 1):
            window = haystack[start : start + width]
            if len(window) < width and width > low:
                break
            matcher.set_seq1(window)
            if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                continue
            if matcher.ratio() >= threshold:
                return True
    return False


def verify_quote(quote: str | None, text: str, threshold: float = QUOTE_FUZZY) -> bool:
    """Цитата модели действительно есть в тексте (точно или нечётко ≥ ``threshold``).

    Цитата из нескольких кусков через «…» засчитывается, если найден каждый кусок.
    """
    cleaned = _clean_quote(quote)
    if not cleaned:
        return False
    haystack = normalize_for_quote(text)
    parts = [normalize_for_quote(part) for part in re.split(r"\.\.\.|…", cleaned)]
    parts = [part.strip(" ,;:") for part in parts if part.strip(" ,;:")]
    return bool(parts) and all(_fuzzy_found(part, haystack, threshold) for part in parts)


_TRANSLIT = (
    ("sch", "ш"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"),
    ("ya", "я"), ("yu", "ю"), ("yo", "е"), ("ph", "ф"), ("ck", "к"), ("ee", "и"), ("oo", "у"),
    ("a", "а"), ("b", "б"), ("c", "к"), ("d", "д"), ("e", "е"), ("f", "ф"), ("g", "г"),
    ("h", "х"), ("i", "и"), ("j", "дж"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"),
    ("o", "о"), ("p", "п"), ("q", "к"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"),
    ("v", "в"), ("w", "в"), ("x", "кс"), ("y", "и"), ("z", "з"),
)  # fmt: skip


def _translit(text: str) -> str:
    """Грубая латиница → кириллица: «makita» → «макита». Только для проверки упоминания."""
    result = text.casefold()
    for latin, cyrillic in _TRANSLIT:
        result = result.replace(latin, cyrillic)
    return result


def _compact(text: str) -> str:
    return re.sub(r"[^0-9a-zа-я]", "", text.casefold().replace("ё", "е"))


def is_mentioned(value: str | None, text: str) -> bool:
    """Бренд/модель/значение действительно написаны в тексте (с точностью до записи)."""
    if not value:
        return False
    compact = _compact(value)
    if compact and compact in _compact(text):
        return True
    haystack = normalize_for_quote(text)
    needle = normalize_for_quote(value)
    return _fuzzy_found(needle, haystack, MENTION_FUZZY) or _fuzzy_found(
        _translit(needle), haystack, MENTION_FUZZY
    )


# --- значения ------------------------------------------------------------------------------

#: Пробелы внутри числа: обычный, неразрывный и узкий неразрывный (копипаст из документов).
_SPACE = r"[ \u00a0\u202f]"
_NUMBER = re.compile(
    r"^\s*(?:около|примерно|до|от|не более|не менее)?\s*[~≈≥≤<>=]?\s*"
    r"(-?(?:\d{1,3}(?:" + _SPACE + r"\d{3})+|\d+)(?:[.,]\d+)?)\s*(.*)$",
    re.IGNORECASE,
)


def parse_llm_number(value: Any) -> tuple[float | None, str | None]:
    """Число из ответа модели и хвост-единица: 840 · «0,84» · «1 200» · «125мм» → (125, «мм»).

    Пробел внутри числа — разделитель тысяч, только если за ним ровно три цифры: «1 200» —
    1200, а «12 5» — 12 (хвост с цифрами единицей не считается).
    """
    if isinstance(value, bool) or value is None:
        return None, None
    if isinstance(value, int | float):
        return (float(value), None) if math.isfinite(value) else (None, None)
    if isinstance(value, list):
        return parse_llm_number(value[0]) if value else (None, None)
    match = _NUMBER.match(str(value))
    if not match:
        return None, None
    digits = re.sub(_SPACE, "", match.group(1)).replace(",", ".")
    try:
        number = float(digits)
    except ValueError:
        return None, None
    tail = match.group(2).strip().strip(".,;:")
    if not tail or re.search(r"\d", tail):
        return number, None
    return number, tail


def unit_from_quote(quote: str | None, number: float | None) -> str | None:
    """Единица после этого числа в цитате: модель вернула unit = null, а в тексте «0,84 кВт»."""
    if not quote or number is None:
        return None
    for match in re.finditer(r"(\d+(?:" + _SPACE + r"\d{3})*(?:[.,]\d+)?)\s*", quote):
        parsed, _ = parse_llm_number(match.group(1))
        if parsed is None or not math.isclose(parsed, number, rel_tol=1e-9, abs_tol=1e-9):
            continue
        words = quote[match.end() :].split()[:3]
        for count in range(len(words), 0, -1):
            candidate = " ".join(words[:count]).strip(".,;:()")
            if candidate and U.parse_unit(candidate) is not None:
                return candidate
    return None


#: Латиница, неотличимая от кириллицы на глаз: «E4» и «Е4» в КТРУ — разные строки.
_LOOKALIKES = str.maketrans("aeopcxykmtbh", "аеорсхукмтвн")


def _glyphs(text: str) -> str:
    """Ключ сравнения, устойчивый к смешению латиницы и кириллицы (раскладка, копипаст)."""
    return text.translate(_LOOKALIKES)


def _negated(text: str) -> bool:
    """Начинается с отрицания: «не …», «без …» (но не «небольшой»)."""
    return re.match(r"(?:не|без)\b", text) is not None


def _number_like(text: str) -> bool:
    return bool(text) and re.fullmatch(r"[\d\s.,/]+", text) is not None


def coerce_text_value(value: str, allowed: list[str]) -> str:
    """Привести качественное значение модели к допустимому КТРУ; не вышло — вернуть как есть.

    Порядок: точное совпадение (регистр, ё, кавычки) → difflib ≥ :data:`ENUM_FUZZY` →
    значение модели — целое слово(а) ровно одного допустимого («углошлифовальная» →
    «Углошлифовальная машина») → допустимое целиком внутри значения модели («куртка утеплённая»
    → «Куртка»). Не приведённое значение остаётся строкой — его отметят как недопустимое.
    """
    text = value.strip()
    if not allowed or not text:
        return text
    folded = _glyphs(fold_text(text))
    folded_allowed = [(item, _glyphs(fold_text(item))) for item in allowed]
    for item, candidate in folded_allowed:
        if candidate == folded:
            return item
    # Отрицание меняет смысл при почти полном совпадении букв: «не металлический» похож на
    # «Металлический» на 90 %, но это противоположное значение.
    negated = _negated(folded)
    folded_allowed = [pair for pair in folded_allowed if _negated(pair[1]) == negated]
    best, best_ratio = None, 0.0
    for item, candidate in folded_allowed:
        ratio = difflib.SequenceMatcher(None, folded, candidate, autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = item, ratio
    if best is not None and best_ratio >= ENUM_FUZZY:
        return best
    if len(folded) >= 5:
        pattern = r"(?<!\w)" + re.escape(folded) + r"(?!\w)"
        containing = [item for item, candidate in folded_allowed if re.search(pattern, candidate)]
        if len(containing) == 1:
            return containing[0]
    inside: list[tuple[int, str]] = []
    for item, candidate in folded_allowed:
        if len(candidate) < 4 and not _number_like(candidate):
            continue
        pattern = r"(?<![\w/.,])" + re.escape(candidate) + r"(?![\w/]|[.,]\d)"
        hit = re.search(pattern, folded)
        before = folded[: hit.start()].split() if hit else []
        if hit and not (before and before[-1] in {"не", "без"}):
            inside.append((len(candidate), item))
    if inside:
        return max(inside)[1]
    return text


def _to_values(char: FamilyChar, item: dict, text: str, source: Source) -> list[ExtractedValue]:
    """Элемент ответа модели → значение(я) характеристики с проверенной цитатой.

    Обычно одно значение; несколько — когда модель дала список для характеристики с выбором
    одного значения: первое — значение, остальные — кандидаты в DUPLICATE_VALUE.
    """
    status = Status(item["status"])
    if status == Status.ABSENT:
        return [ExtractedValue(char_key=char.key, status=Status.ABSENT, source=source)]
    quote = _clean_quote(item.get("quote"))
    quote_found = verify_quote(quote, text)
    raw = item.get("value")
    unit = _opt_str(item.get("unit"))

    values: list[str | float | list[str] | None]
    if char.is_numeric:
        number, tail = parse_llm_number(raw)
        if unit is None and tail and U.parse_unit(tail) is not None:
            unit = tail
        if unit is None:
            unit = unit_from_quote(quote, number)
        values = [number]
    else:
        allowed = char.text_values()
        texts = raw if isinstance(raw, list) else ([] if raw is None else [raw])
        coerced: list[str] = []
        for entry in texts:
            entry_text = _opt_str(entry)
            if entry_text:
                mapped = coerce_text_value(entry_text, allowed)
                if fold_text(mapped) not in {fold_text(seen) for seen in coerced}:
                    coerced.append(mapped)
        unit = None
        if P.is_multi(char):
            values = [coerced or None]
        else:
            values = list(coerced) or [None]

    result: list[ExtractedValue] = []
    for value in values:
        entry_status = status
        if value is None and status == Status.SPECIFIED:
            # «Указано», но значения нет — опереться не на что.
            entry_status = Status.ABSENT
        confident = entry_status == Status.SPECIFIED and quote_found
        result.append(
            ExtractedValue(
                char_key=char.key,
                status=entry_status,
                value=value,
                unit=unit if char.is_numeric and entry_status != Status.ABSENT else None,
                quote=quote if entry_status != Status.ABSENT else None,
                quote_found=quote_found if entry_status != Status.ABSENT else False,
                source=source,
                confidence=Confidence.CONFIDENT if confident else Confidence.WEAK,
            )
        )
    return result


def _same(left: ExtractedValue, right: ExtractedValue) -> bool:
    if isinstance(left.value, float) and isinstance(right.value, float):
        same_unit = fold_text(left.unit or "") == fold_text(right.unit or "")
        return math.isclose(left.value, right.value) and same_unit
    if isinstance(left.value, list) and isinstance(right.value, list):
        return {fold_text(item) for item in left.value} == {fold_text(item) for item in right.value}
    return fold_text(str(left.value)) == fold_text(str(right.value))


def _merge(
    values: dict[str, ExtractedValue],
    duplicates: dict[str, list[ExtractedValue]],
    char: FamilyChar,
    new: ExtractedValue,
) -> None:
    current = values.get(char.key)
    if current is None or current.status == Status.ABSENT:
        values[char.key] = new
        return
    if new.status == Status.ABSENT or _same(current, new):
        if new.status != Status.ABSENT and new.quote_found and not current.quote_found:
            values[char.key] = new
        return
    if P.is_multi(char) and isinstance(current.value, list) and isinstance(new.value, list):
        known = {fold_text(item) for item in current.value}
        current.value = current.value + [item for item in new.value if fold_text(item) not in known]
        if new.quote and new.quote != current.quote:
            current.quote = f"{current.quote} … {new.quote}" if current.quote else new.quote
        current.quote_found = current.quote_found and new.quote_found
        if current.status == Status.SPECIFIED and current.quote_found:
            current.confidence = Confidence.CONFIDENT
        else:
            current.confidence = Confidence.WEAK
        return
    duplicates.setdefault(char.key, []).append(new)


# --- схемы ответов -------------------------------------------------------------------------

_STATUS_ENUM = [status.value for status in Status]
_VALUE = {
    "anyOf": [
        {"type": "string"},
        {"type": "number"},
        {"type": "array", "items": {"type": "string"}},
        {"type": "null"},
    ]
}


def selection_schema(allowed_ids: list[str]) -> dict:
    candidate = {
        "type": "object",
        "properties": {
            "family_id": {
                "type": "string",
                "enum": list(allowed_ids),
                "description": "id семейства из списка",
            },
            "score": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
        },
        "required": ["family_id", "score", "reason"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "candidates": {"type": "array", "items": candidate, "maxItems": MAX_CANDIDATES},
            "not_found": {"type": "boolean"},
            "not_found_reason": {"type": ["string", "null"]},
        },
        "required": ["candidates", "not_found", "not_found_reason"],
        "additionalProperties": False,
    }


def _value_item(ids: list[str] | None) -> dict:
    properties: dict[str, Any] = {}
    if ids is not None:
        properties["id"] = {
            "type": "string",
            "enum": list(ids),
            "description": "id характеристики из таблицы",
        }
    properties.update(
        status={"type": "string", "enum": _STATUS_ENUM},
        value=_VALUE,
        unit={"type": ["string", "null"]},
        quote={"type": ["string", "null"]},
    )
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_EXTRA_ITEM = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "value": {"type": "string"},
        "unit": {"type": ["string", "null"]},
        "quote": {"type": "string"},
    },
    "required": ["name", "value", "unit", "quote"],
    "additionalProperties": False,
}


def extraction_schema(ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "characteristics": {"type": "array", "items": _value_item(ids)},
            "extra": {"type": "array", "items": _EXTRA_ITEM},
            "brand": {"type": ["string", "null"]},
            "model": {"type": ["string", "null"]},
        },
        "required": ["characteristics", "extra", "brand", "model"],
        "additionalProperties": False,
    }


def one_schema() -> dict:
    return _value_item(None)


# --- приведение ответа к схеме до проверки -------------------------------------------------

_STATUS_ALIASES = {
    "указано": Status.SPECIFIED,
    "указан": Status.SPECIFIED,
    "указана": Status.SPECIFIED,
    "specified": Status.SPECIFIED,
    "found": Status.SPECIFIED,
    "не указано": Status.ABSENT,
    "не указан": Status.ABSENT,
    "не указана": Status.ABSENT,
    "неуказано": Status.ABSENT,
    "нет": Status.ABSENT,
    "absent": Status.ABSENT,
    "missing": Status.ABSENT,
    "not specified": Status.ABSENT,
    "противоречие": Status.CONTRADICTION,
    "contradiction": Status.CONTRADICTION,
    "conflict": Status.CONTRADICTION,
}


def _status(raw: Any, value: Any) -> str:
    key = fold_text(str(raw or "")).replace("_", " ").replace("-", " ")
    if key in _STATUS_ALIASES:
        return _STATUS_ALIASES[key].value
    if key.startswith("противореч"):
        return Status.CONTRADICTION.value
    if key.startswith("не "):
        return Status.ABSENT.value
    if key.startswith("указ"):
        return Status.SPECIFIED.value
    has_value = value not in (None, "", [])
    return (Status.SPECIFIED if has_value else Status.ABSENT).value


def _value_entry(entry: dict) -> dict:
    value = _clean_value(entry.get("value"))
    return {
        "status": _status(entry.get("status"), value),
        "value": value,
        "unit": _opt_str(entry.get("unit")),
        "quote": _opt_str(entry.get("quote")),
    }


def _char_resolver(family: Family, ids: dict[str, str]):
    """id из ответа → наш id: «c1», «C01», кириллическая «с01» и даже имя характеристики."""
    width = len(next(iter(ids))) - 1 if ids else 2
    by_name = {key: cid for cid, key in ids.items()}
    by_name.update({make_char_key(family.chars[key].name): cid for cid, key in ids.items()})

    def resolve(raw: Any) -> str | None:
        if raw is None:
            return None
        text = str(raw).strip()
        match = re.match(r"\s*[cCсС]\s*0*(\d+)(?!\d)", text)
        if match:
            cid = f"c{int(match.group(1)):0{width}d}"
            return cid if cid in ids else None
        return by_name.get(make_char_key(text))

    return resolve


def _extra_entries(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    result = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = _opt_str(entry.get("name"))
        value = _opt_str(entry.get("value"))
        if not name or not value:
            continue
        result.append(
            {
                "name": name,
                "value": value,
                "unit": _opt_str(entry.get("unit")),
                "quote": _opt_str(entry.get("quote")) or "",
            }
        )
    return result


def _prepare_extraction(family: Family, ids: dict[str, str]):
    resolve = _char_resolver(family, ids)

    def prepare(obj: Any) -> dict:
        if not isinstance(obj, dict):
            raise ValueError("ожидался JSON-объект")
        raw = obj.get("characteristics", obj.get("values"))
        entries: list[dict] = []
        if isinstance(raw, dict):  # формат §3.6: {"c01": {...}} или {"c01": "Куртка"}
            for key, entry in raw.items():
                if isinstance(entry, dict):
                    entries.append({**entry, "id": key})
                elif entry is not None:
                    entries.append({"id": key, "value": entry})
        elif isinstance(raw, list):
            entries = [entry for entry in raw if isinstance(entry, dict)]
        elif raw is not None:
            raise ValueError("characteristics: ожидался список")
        items = []
        for entry in entries:
            cid = resolve(entry.get("id", entry.get("name")))
            if cid is not None:
                items.append({"id": cid, **_value_entry(entry)})
        return {
            "characteristics": items,
            "extra": _extra_entries(obj.get("extra")),
            "brand": _opt_str(obj.get("brand")),
            "model": _opt_str(obj.get("model")),
        }

    return prepare


def _prepare_one(obj: Any) -> dict:
    if not isinstance(obj, dict):
        raise ValueError("ожидался JSON-объект")
    if "status" not in obj and "value" not in obj:
        # Модель могла ответить в формате LLM-2: {"characteristics": [{...}]} или {"c01": {...}}.
        nested = obj.get("characteristics")
        if isinstance(nested, list) and nested and isinstance(nested[0], dict):
            obj = nested[0]
        else:
            inner = [value for value in obj.values() if isinstance(value, dict)]
            if len(inner) == 1:
                obj = inner[0]
    return _value_entry(obj)


def _score(raw: Any) -> float:
    try:
        value = float(str(raw).strip().rstrip("%").replace(",", "."))
    except (TypeError, ValueError):
        return 0.5
    if math.isnan(value):
        return 0.5
    if 1 < value <= 100:  # «85» или «85%» вместо 0.85
        value /= 100
    return min(1.0, max(0.0, value))


def _prepare_selection(allowed: list[str]):
    allowed_set = set(allowed)
    by_number: dict[int, str] = {}
    for item in allowed:
        match = re.fullmatch(r"[A-Za-z]+0*(\d+)", item)
        if match:
            by_number.setdefault(int(match.group(1)), item)

    def resolve(raw: Any) -> str | None:
        if raw is None:
            return None
        text = str(raw).strip()
        if text in allowed_set:
            return text
        match = re.search(r"(?<![A-Za-zА-Яа-я])[FfФф]\s*-?\s*0*(\d+)(?!\d)", text)
        return by_number.get(int(match.group(1))) if match else None

    def prepare(obj: Any) -> dict:
        if isinstance(obj, list):
            obj = {"candidates": obj}
        if not isinstance(obj, dict):
            raise ValueError("ожидался JSON-объект")
        raw = obj.get("candidates")
        if raw is None and "family_id" in obj:
            raw = [obj]
        if isinstance(raw, dict):
            raw = [raw]
        best: dict[str, dict] = {}
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict):
                continue
            family_id = resolve(entry.get("family_id", entry.get("id")))
            if family_id is None:
                continue
            candidate = {
                "family_id": family_id,
                "score": _score(entry.get("score", entry.get("confidence"))),
                "reason": _opt_str(entry.get("reason")) or "",
            }
            if family_id not in best or best[family_id]["score"] < candidate["score"]:
                best[family_id] = candidate
        ordered = sorted(best.values(), key=lambda item: -item["score"])[:MAX_CANDIDATES]
        not_found = _bool(obj.get("not_found"), default=not ordered)
        return {
            "candidates": ordered,
            "not_found": not_found,
            "not_found_reason": _opt_str(obj.get("not_found_reason")),
        }

    return prepare


# --- шаги ----------------------------------------------------------------------------------


def select_family(
    client: JsonLlm,
    text: str,
    card_lines: list[str],
    generic_lines: list[str],
    allowed_ids: list[str],
) -> SelectionResult:
    """LLM-1 (§4.1): кандидаты-семейства. Правила решения по порогам — в пайплайне."""
    allowed = list(dict.fromkeys(allowed_ids))
    if not text.strip():
        return SelectionResult([], True, "Пустое описание товара")
    if not allowed:
        return SelectionResult([], True, "Нет семейств КТРУ для выбора")
    response = client.complete_json(
        P.family_select_messages(text, card_lines, generic_lines),
        selection_schema(allowed),
        "select_family",
        max_tokens=SELECT_MAX_TOKENS,
        temperature=0.0,
        prepare=_prepare_selection(allowed),
    )
    data = response.data
    candidates = [
        (item["family_id"], float(item["score"]), item["reason"]) for item in data["candidates"]
    ]
    not_found = bool(data["not_found"]) or not candidates
    reason = data.get("not_found_reason") if not_found else None
    if not_found and not reason:
        reason = "Модель не нашла подходящего семейства КТРУ"
    return SelectionResult(candidates, not_found, reason, response)


def _absent_all(family: Family, source: Source = Source.TEXT) -> dict[str, ExtractedValue]:
    return {
        key: ExtractedValue(char_key=key, status=Status.ABSENT, source=source)
        for key in family.chars
    }


def extract_values(client: JsonLlm, text: str, family: Family) -> ExtractionResult:
    """LLM-2 (§4.3): значения всех характеристик семейства, extra, бренд и модель."""
    if not text.strip() or not family.chars:
        return ExtractionResult(values=_absent_all(family), extra=[])
    ids = P.char_ids(family)
    response = client.complete_json(
        P.extract_messages(text, family),
        extraction_schema(list(ids)),
        "extract_values",
        max_tokens=EXTRACT_MAX_TOKENS,
        temperature=0.0,
        prepare=_prepare_extraction(family, ids),
    )
    data = response.data
    values: dict[str, ExtractedValue] = {}
    duplicates: dict[str, list[ExtractedValue]] = {}
    for item in data["characteristics"]:
        char = family.chars[ids[item["id"]]]
        for value in _to_values(char, item, text, Source.TEXT):
            _merge(values, duplicates, char, value)

    by_name = {make_char_key(char.name): char for char in family.chars.values()}
    extra: list[dict] = []
    for entry in data["extra"]:
        char = by_name.get(make_char_key(entry["name"]))
        if char is not None:
            # Характеристика из таблицы, которую модель положила в extra: так теряется проверка
            # значения по КТРУ — возвращаем её в характеристики.
            item = {"status": Status.SPECIFIED.value, **entry}
            for value in _to_values(char, item, text, Source.TEXT):
                _merge(values, duplicates, char, value)
            continue
        if not (verify_quote(entry["quote"], text) or is_mentioned(entry["value"], text)):
            continue  # опоры в тексте нет — выдумано
        name = entry["name"]
        extra.append(
            {
                "name": name[:1].upper() + name[1:],
                "value": entry["value"],
                "unit": entry["unit"],
                "quote": _clean_quote(entry["quote"]) or entry["value"],
            }
        )

    for key, value in _absent_all(family).items():
        values.setdefault(key, value)
    ordered = {key: values[key] for key in family.chars}
    brand = data["brand"] if is_mentioned(data["brand"], text) else None
    model = data["model"] if is_mentioned(data["model"], text) else None
    return ExtractionResult(ordered, extra, brand, model, response=response, duplicates=duplicates)


def extract_one(client: JsonLlm, text: str, family: Family, char_key: str) -> ExtractedValue | None:
    """Мини-LLM-2 (§4.8): значение одной характеристики из свободного ответа пользователя.

    ``None`` — в ответе значения нет («не знаю»): решать, что делать, — пайплайну.
    """
    char = family.chars.get(char_key)
    if char is None:
        raise KeyError(f"У семейства «{family.name}» нет характеристики «{char_key}»")
    if not text.strip():
        return None
    response = client.complete_json(
        P.extract_one_messages(text, family, char_key),
        one_schema(),
        "extract_one",
        max_tokens=ONE_MAX_TOKENS,
        temperature=0.0,
        prepare=_prepare_one,
    )
    values = _to_values(char, dict(response.data), text, Source.ANSWER)
    value = values[0] if values else None
    if value is None or value.status == Status.ABSENT:
        return None
    return value
