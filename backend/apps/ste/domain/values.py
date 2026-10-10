"""Допустимые значения характеристики КТРУ и проверка значения пользователя против них.

Три формы значения в КТРУ (архитектура §1.5):

* ``text`` — качественное, ``qualityDescription``;
* ``range`` — интервал ``rangeSet`` с границами ``greater`` (>), ``greaterOrEqual`` (≥),
  ``less`` (<), ``lessOrEqual`` (≤); граница может отсутствовать;
* ``concrete`` — точное число ``valueSet/concreteValue``.

Спутать строгую и нестрогую границу — значит принять значение, которое КТРУ отвергает:
125 мм для «≥115 <125» — вне интервала, а для «≥125 <130» — внутри.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from apps.ste.domain import units as U

#: Допуск сравнения с точным значением (архитектура §4.4): 0,5 %.
CONCRETE_TOLERANCE = 0.005


def fold_text(text: str) -> str:
    """Ключ сравнения текстов: регистр, ё/е, пробелы, кавычки."""
    folded = text.casefold().replace("ё", "е")
    folded = re.sub(r"[\"«»„“”']", "", folded)
    return re.sub(r"\s+", " ", folded).strip(" .")


def format_number(value: float) -> str:
    """125.0 → «125», 0.5 → «0,5». Запятая — русская запись."""
    if value is None:
        return ""
    if math.isfinite(value) and value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}".replace(".", ",")


@dataclass(frozen=True)
class AllowedValue:
    kind: str  # "text" | "range" | "concrete"
    text: str | None = None
    minimum: float | None = None
    min_inclusive: bool = True
    maximum: float | None = None
    max_inclusive: bool = True
    concrete: float | None = None
    #: Код ОКЕИ значения (или псевдокод единицы из названия характеристики).
    okei: str | None = None
    unit_name: str | None = None

    # --- сериализация ----------------------------------------------------------------------

    def to_json(self) -> dict:
        if self.kind == "text":
            return {"t": "text", "text": self.text}
        data: dict = {"t": self.kind, "okei": self.okei, "unit": self.unit_name}
        if self.kind == "range":
            data.update(
                min=self.minimum,
                min_inc=self.min_inclusive,
                max=self.maximum,
                max_inc=self.max_inclusive,
            )
        else:
            data["value"] = self.concrete
        return data

    @classmethod
    def from_json(cls, data: dict) -> AllowedValue:
        kind = data["t"]
        if kind == "text":
            return cls(kind="text", text=data["text"])
        if kind == "range":
            return cls(
                kind="range",
                minimum=data.get("min"),
                min_inclusive=data.get("min_inc", True),
                maximum=data.get("max"),
                max_inclusive=data.get("max_inc", True),
                okei=data.get("okei"),
                unit_name=data.get("unit"),
            )
        return cls(
            kind="concrete",
            concrete=data.get("value"),
            okei=data.get("okei"),
            unit_name=data.get("unit"),
        )

    # --- свойства --------------------------------------------------------------------------

    @property
    def is_numeric(self) -> bool:
        return self.kind in ("range", "concrete")

    @property
    def unit(self) -> U.Unit | None:
        return U.by_okei(self.okei, self.unit_name)

    @property
    def key(self) -> str:
        """Ключ для сравнения и дедупликации."""
        if self.kind == "text":
            return "t:" + fold_text(self.text or "")
        if self.kind == "range":
            return (
                f"r:{self.minimum}:{self.min_inclusive}:{self.maximum}:{self.max_inclusive}:"
                f"{self.okei}"
            )
        return f"c:{self.concrete}:{self.okei}"

    # --- проверка --------------------------------------------------------------------------

    def contains_number(self, value: float) -> bool:
        """Попадает ли число (уже в единице этого значения) в допустимое."""
        if self.kind == "concrete":
            if self.concrete is None:
                return False
            if self.concrete == 0:
                return abs(value) < 1e-9
            return abs(value - self.concrete) <= abs(self.concrete) * CONCRETE_TOLERANCE
        if self.kind != "range":
            return False
        if self.minimum is not None:
            if self.min_inclusive and value < self.minimum:
                return False
            if not self.min_inclusive and value <= self.minimum:
                return False
        if self.maximum is not None:
            if self.max_inclusive and value > self.maximum:
                return False
            if not self.max_inclusive and value >= self.maximum:
                return False
        return True

    def matches_text(self, value: str) -> bool:
        return self.kind == "text" and fold_text(self.text or "") == fold_text(value)

    def distance(self, value: float) -> float:
        """Насколько число далеко от допустимого (0 — внутри). Для «ближайшего допустимого»."""
        if self.contains_number(value):
            return 0.0
        if self.kind == "concrete":
            return abs(value - (self.concrete or 0.0))
        low = self.minimum if self.minimum is not None else -math.inf
        high = self.maximum if self.maximum is not None else math.inf
        if value < low:
            return low - value
        if value > high:
            return value - high
        # на строгой границе
        return 0.0 if low == high else 1e-9

    def describe(self) -> str:
        """Человекочитаемая запись, как в карточке: «≥125 <130 Миллиметр», «= 2 Штука»."""
        if self.kind == "text":
            return self.text or ""
        unit = self.unit_name_short
        if self.kind == "concrete":
            return f"= {format_number(self.concrete)} {unit}".strip()
        parts = []
        if self.minimum is not None:
            parts.append(("≥" if self.min_inclusive else ">") + format_number(self.minimum))
        if self.maximum is not None:
            parts.append(("≤" if self.max_inclusive else "<") + format_number(self.maximum))
        if not parts:
            parts.append("любое значение")
        return (" ".join(parts) + " " + unit).strip()

    @property
    def unit_name_short(self) -> str:
        unit = self.unit
        if unit is not None:
            return unit.name
        return (self.unit_name or "").split("(")[0].split(";")[0].strip()


def describe_all(values: list[AllowedValue], limit: int = 12) -> str:
    """Список допустимых значений одной строкой — для объяснений в ошибках."""
    shown = [value.describe() for value in values[:limit]]
    more = len(values) - len(shown)
    text = "; ".join(shown)
    return text + (f" и ещё {more}" if more > 0 else "")


def nearest(values: list[AllowedValue], value: float) -> AllowedValue | None:
    """Ближайшее допустимое числовое значение (в одной с ``value`` единице)."""
    numeric = [item for item in values if item.is_numeric]
    if not numeric:
        return None
    return min(numeric, key=lambda item: item.distance(value))
