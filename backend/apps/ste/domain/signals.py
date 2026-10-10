"""Модуль ошибок: сигналы о несоответствиях с обоснованием (архитектура §8.2).

Минимальная версия от интегратора — API зафиксирован; модуль расширяется отдельно.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from apps.ste.domain import units as U
from apps.ste.domain.model import (
    ExtractedValue,
    NormalizedValue,
    Severity,
    Signal,
    SignalType,
    Status,
    TypoFix,
)
from apps.ste.domain.values import describe_all, format_number, nearest

_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def _user_value(ev: ExtractedValue, nv: NormalizedValue | None) -> str:
    if nv is not None and nv.user_text:
        return nv.user_text
    if isinstance(ev.value, list):
        return ", ".join(str(item) for item in ev.value)
    if isinstance(ev.value, float):
        return format_number(ev.value) + (f" {ev.unit}" if ev.unit else "")
    return str(ev.value or "")


def value_signals(spec, ev: ExtractedValue, nv: NormalizedValue, family_id: str | None = None):
    out: list[Signal] = []
    user = _user_value(ev, nv)
    base = dict(
        char=spec.name,
        char_key=spec.key,
        user_value=user,
        user_unit=ev.unit,
        quote=ev.quote,
        family_id=family_id,
    )
    if ev.status == Status.CONTRADICTION:
        out.append(
            Signal(
                type=SignalType.CONTRADICTION,
                severity=Severity.ERROR,
                message=f"«{spec.name}»: описание противоречит само себе или справочнику КТРУ.",
                suggestion="Уточните значение характеристики.",
                allowed=[value.describe() for value in spec.allowed][:12],
                **base,
            )
        )
        return out
    if ev.quote and not ev.quote_found:
        out.append(
            Signal(
                type=SignalType.QUOTE_NOT_FOUND,
                severity=Severity.INFO,
                message=f"«{spec.name}»: значение не подтверждено цитатой из текста.",
                **base,
            )
        )
    if spec.is_numeric and nv.number is not None:
        unit = U.parse_unit(ev.unit) if ev.unit else None
        char_unit = spec.unit
        if ev.unit and unit is None:
            out.append(
                Signal(
                    type=SignalType.UNKNOWN_UNIT,
                    severity=Severity.WARNING,
                    message=f"«{spec.name}»: единица «{ev.unit}» не распознана.",
                    suggestion=(
                        f"Укажите значение в единицах «{char_unit.name}»." if char_unit else None
                    ),
                    **base,
                )
            )
        elif unit is not None and char_unit is not None and unit.dimension != char_unit.dimension:
            out.append(
                Signal(
                    type=SignalType.UNIT_MISMATCH,
                    severity=Severity.ERROR,
                    message=(
                        f"«{spec.name}» измеряется как "
                        f"{U.DIMENSION_NAMES.get(char_unit.dimension, char_unit.dimension)} "
                        f"({char_unit.name}), а «{ev.unit}» — это "
                        f"{U.DIMENSION_NAMES.get(unit.dimension, unit.dimension)}."
                    ),
                    suggestion=f"Укажите значение в единицах «{char_unit.name}».",
                    **base,
                )
            )
        elif nv.unit_ok and not nv.matched and any(value.is_numeric for value in spec.allowed):
            value_in_unit = nv.number if nv.number_in_ktru_unit is None else nv.number_in_ktru_unit
            near = nearest(list(spec.allowed), value_in_unit)
            out.append(
                Signal(
                    type=SignalType.OUT_OF_RANGE,
                    severity=Severity.ERROR,
                    message=(
                        f"«{spec.name}» = {user}: такого значения нет в КТРУ. Допустимо: "
                        f"{describe_all(list(spec.allowed))}."
                    ),
                    allowed=[value.describe() for value in spec.allowed][:12],
                    nearest_allowed=near.describe() if near else None,
                    suggestion=f"Ближайшее допустимое: {near.describe()}." if near else None,
                    **base,
                )
            )
    elif not spec.is_numeric and user and not nv.texts:
        options = [value.text for value in spec.allowed if value.kind == "text" and value.text]
        close = difflib.get_close_matches(user, options, n=1, cutoff=0.4)
        out.append(
            Signal(
                type=SignalType.VALUE_NOT_ALLOWED,
                severity=Severity.ERROR,
                message=(
                    f"«{spec.name}» = «{user}»: такого значения нет в КТРУ. Допустимо: "
                    f"{'; '.join(options[:12])}."
                ),
                allowed=options[:12],
                nearest_allowed=close[0] if close else None,
                suggestion=f"Возможно, имелось в виду «{close[0]}»." if close else None,
                **base,
            )
        )
    return out


def duplicate_signals(values: list[ExtractedValue], names: dict[str, str]) -> list[Signal]:
    seen: dict[str, ExtractedValue] = {}
    out: list[Signal] = []
    for ev in values:
        known = seen.get(ev.char_key)
        if known is not None and known.value != ev.value:
            name = names.get(ev.char_key, ev.char_key)
            out.append(
                Signal(
                    type=SignalType.DUPLICATE_VALUE,
                    severity=Severity.WARNING,
                    message=f"«{name}» указана дважды: «{known.value}» и «{ev.value}».",
                    suggestion="Оставьте одно значение.",
                    char=name,
                    char_key=ev.char_key,
                )
            )
        seen.setdefault(ev.char_key, ev)
    return out


@dataclass
class ConflictFact:
    char_key: str
    char_name: str
    user_value: str
    excludes: int
    nearest_allowed: str | None


def conflict_signal(facts: list[ConflictFact], family_name: str) -> Signal:
    names = ", ".join(f"«{fact.char_name}» = {fact.user_value}" for fact in facts)
    near = next((fact for fact in facts if fact.nearest_allowed), None)
    return Signal(
        type=SignalType.CONFLICT,
        severity=Severity.ERROR,
        message=(f"Значения вместе исключают все конечные позиции «{family_name}»: {names}."),
        char=facts[0].char_name if facts else None,
        char_key=facts[0].char_key if facts else None,
        user_value=facts[0].user_value if facts else None,
        nearest_allowed=near.nearest_allowed if near else None,
        suggestion=(
            f"Исправьте «{near.char_name}», ближайшее допустимое: {near.nearest_allowed}."
            if near
            else "Исправьте одно из значений."
        ),
    )


def missing_required_signals(missing, leaf_code: str | None) -> list[Signal]:
    out = []
    for key, name, options in missing:
        out.append(
            Signal(
                type=SignalType.MISSING_REQUIRED,
                severity=Severity.WARNING,
                message=f"Не указана обязательная характеристика «{name}».",
                char=name,
                char_key=key,
                allowed=list(options)[:8],
                suggestion=("Варианты: " + "; ".join(list(options)[:8])) if options else None,
                leaf_code=leaf_code,
            )
        )
    return out


def not_found_signal(text: str, suggestions: list[str]) -> Signal:
    hint = f" Возможно, вы имели в виду: {', '.join(suggestions)}." if suggestions else ""
    return Signal(
        type=SignalType.NOT_FOUND,
        severity=Severity.WARNING,
        message="Тип товара не найден среди позиций КТРУ (одежда, обувь, мебель, инструменты)."
        + hint,
        suggestion="Уточните, что это за товар, и укажите основные характеристики.",
        user_value=text,
    )


def typo_signal(fix: TypoFix) -> Signal:
    if fix.reason == "layout":
        message = f"Текст набран в другой раскладке: «{fix.original}» → «{fix.corrected}»."
    else:
        message = f"Исправлена опечатка: «{fix.original}» → «{fix.corrected}»."
    return Signal(
        type=SignalType.TYPO,
        severity=Severity.INFO,
        message=message,
        user_value=fix.original,
        suggestion=fix.corrected,
    )


def sort_signals(signals: list[Signal]) -> list[Signal]:
    return sorted(signals, key=lambda signal: _ORDER.get(signal.severity, 3))


def summarize(signals: list[Signal]) -> str:
    errors = sum(1 for signal in signals if signal.severity == Severity.ERROR)
    warnings = sum(1 for signal in signals if signal.severity == Severity.WARNING)
    parts = []
    if errors:
        parts.append(f"ошибок: {errors}")
    if warnings:
        parts.append(f"предупреждений: {warnings}")
    return ", ".join(parts) or "ошибок не найдено"
