"""Карточка СТЕ (архитектура §4.7): характеристики КТРУ с источником значения + дополнительные.

Карточка показывает пользователю, **что было унифицировано**: для каждой характеристики —
как написал пользователь («0,84 кВт»), во что это превратилось в КТРУ («840 Ватт»,
«≥800 <900 Ватт»), откуда значение (текст, ответ, правка, КТРУ) и насколько оно надёжно.

* итоговый лист известен — характеристики листа (его сигнатура §3.4) + дозаполнение §4.6;
* лист ещё не выбран — характеристики семейства, у которых есть значение, или которые
  различают листья, или обязательные (чтобы было видно, что ещё понадобится).

Порядок: kind=1 (определяют лист), затем обязательные, затем остальные.

Модуль чистый: без Django и ввода-вывода.
"""

from __future__ import annotations

from apps.ste.domain import units as U
from apps.ste.domain.kb import CharSpec, Family, FamilyChar, Leaf
from apps.ste.domain.model import NormalizedValue, Source
from apps.ste.domain.normalize import (
    display_value,
    has_content,
    is_interval_choice,
    spec_kinds,
)
from apps.ste.domain.questions import FILL_OPTIONS_LIMIT, FillResult
from apps.ste.domain.values import fold_text, format_number

ACTION_REQUIRED = "дозаполнить"
ACTION_OPTIONAL = "можно заполнить"


def _kind(spec: FamilyChar | CharSpec) -> int:
    """Один вид для карточки: kind=1, если хоть у одного листа неизменяемая; иначе 3 или 2."""
    kinds = spec_kinds(spec)
    if 1 in kinds:
        return 1
    return 3 if kinds == (3,) else 2


def _ordered(specs: list[FamilyChar | CharSpec]) -> list[FamilyChar | CharSpec]:
    return sorted(specs, key=lambda spec: (0 if _kind(spec) == 1 else 1, 0 if spec.required else 1))


def _unified(nv: NormalizedValue) -> bool:
    """Пришлось ли приводить значение к КТРУ.

    Для списка (kind=3) сравниваем поэлементно: «Полка для клавиатуры; Замок…» дословно —
    не унификация, хотя строка пользователя и не равна ни одному значению по отдельности.
    Число с нераспознанной единицей не унифицировано — его не удалось привести.
    """
    if nv.source == Source.KTRU:
        return False
    if nv.value_type == "number":
        return nv.unit_ok and nv.was_unified
    if nv.value_type == "multi" and nv.texts:
        written = {fold_text(part) for part in nv.user_text.replace(",", ";").split(";")}
        return not {fold_text(text) for text in nv.texts} <= written
    return nv.was_unified


def _options(spec: FamilyChar | CharSpec) -> list[str]:
    seen: dict[str, None] = {}
    for value in spec.allowed:
        seen.setdefault(value.describe(), None)
    return list(seen)[:FILL_OPTIONS_LIMIT]


def _row(
    spec: FamilyChar | CharSpec,
    nv: NormalizedValue | None,
    action: str | None,
) -> dict:
    unit = spec.unit if spec.is_numeric else None
    row: dict = {
        "key": spec.key,
        "name": spec.name,
        "value": None,
        "values": [],
        "user_value": None,
        "user_unit": None,
        "ktru_value": None,
        "unit": unit.name if unit else None,
        "source": "none",
        "quote": None,
        "required": bool(spec.required),
        "kind": _kind(spec),
        "confidence": None,
        "unified": False,
        "action": action,
        "options": _options(spec),
    }
    if nv is None:
        return row

    from_ktru = nv.source == Source.KTRU
    row["source"] = nv.source.value
    row["quote"] = nv.quote
    row["confidence"] = None if from_ktru else nv.confidence.value
    row["unified"] = _unified(nv)
    row["ktru_value"] = "; ".join(nv.matched) or None

    if nv.value_type == "number":
        if is_interval_choice(nv):
            # Ответ кнопкой-интервалом или значение из КТРУ: значение — сам интервал.
            row["user_value"] = None if from_ktru else nv.user_text
            row["value"] = nv.user_text
            row["values"] = [nv.user_text]
            return row
        row["user_value"] = (
            format_number(nv.number) if nv.number is not None else nv.user_text or None
        )
        row["user_unit"] = nv.user_unit
        if nv.number is not None and nv.unit_ok:
            number = nv.number_in_ktru_unit if nv.number_in_ktru_unit is not None else nv.number
            row["value"] = format_number(number)
            row["unit"] = nv.ktru_unit or row["unit"]
        row["values"] = [row["value"]] if row["value"] else []
        return row

    if not from_ktru:
        row["user_value"] = nv.user_text or None
    texts = list(nv.texts) if nv.texts else ([nv.user_text] if nv.user_text else [])
    row["values"] = texts
    row["value"] = ", ".join(texts) or None
    return row


def _extra_item(item: dict) -> dict:
    """Дополнительная характеристика в форме контракта; единица — к названию ОКЕИ."""
    name = str(item.get("name") or "").strip()
    value = item.get("value")
    if isinstance(value, list | tuple):
        value = ", ".join(str(part) for part in value)
    elif isinstance(value, float | int) and not isinstance(value, bool):
        value = format_number(float(value))
    unit_text = str(item.get("unit") or "").strip() or None
    unit = U.parse_unit(unit_text) if unit_text else None
    result = {
        "name": name[:1].upper() + name[1:],
        "value": None if value is None else str(value),
        # Нераспознанную единицу не выбрасываем: пусть пользователь увидит, что написал.
        "unit": unit.name if unit else unit_text,
        "quote": item.get("quote"),
    }
    if item.get("matched_from"):
        result["matched_from"] = item["matched_from"]
    return result


def _value_as_extra(nv: NormalizedValue) -> dict:
    """Значение характеристики, которой у итогового листа нет, — в дополнительные."""
    if nv.value_type == "number" and not is_interval_choice(nv):
        value = format_number(nv.number) if nv.number is not None else nv.user_text
        unit = nv.user_unit if nv.number is not None else None
        if nv.unit_ok and nv.number_in_ktru_unit is not None:
            value, unit = format_number(nv.number_in_ktru_unit), nv.ktru_unit
        return {"name": nv.char_name, "value": value, "unit": unit, "quote": nv.quote}
    return {"name": nv.char_name, "value": display_value(nv), "unit": None, "quote": nv.quote}


def build_card(
    family: Family,
    leaf: Leaf | None,
    values: dict[str, NormalizedValue],
    fill: FillResult | None,
    extra: list[dict],
    brand: str | None,
    model: str | None,
) -> dict:
    """Карточка СТЕ — объект ``card`` контракта API."""
    by_ktru = fill.by_ktru if fill else {}
    actions: dict[str, str] = {}
    if fill is not None:
        actions.update({item.key: ACTION_REQUIRED for item in fill.to_fill_required})
        actions.update({item.key: ACTION_OPTIONAL for item in fill.to_fill_optional})

    if leaf is not None:
        specs: list[FamilyChar | CharSpec] = list(leaf.chars.values())
    else:
        specs = [
            char
            for key, char in family.chars.items()
            if (key in values and has_content(values[key])) or char.differentiator or char.required
        ]
    shown = {spec.key for spec in specs}

    rows = []
    for spec in _ordered(specs):
        nv = values.get(spec.key)
        if nv is None or not has_content(nv):
            nv = by_ktru.get(spec.key, nv)
        rows.append(_row(spec, nv, actions.get(spec.key)))

    extras = [_extra_item(item) for item in extra if item.get("name") or item.get("value")]
    extras += [
        _extra_item(_value_as_extra(nv))
        for key, nv in values.items()
        if key not in shown and has_content(nv)
    ]

    okpd2 = leaf.okpd2 if leaf is not None and leaf.okpd2 else family.okpd2
    return {
        "ktru_code": leaf.code if leaf is not None else None,
        "ktru_name": leaf.name if leaf is not None else family.name,
        "okpd2": {"code": okpd2, "name": family.okpd2_name},
        "characteristics": rows,
        "extra": extras,
        "brand": brand or None,
        "model": model or None,
    }
