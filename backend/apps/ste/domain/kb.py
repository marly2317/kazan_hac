"""База знаний, собранная из КТРУ офлайн (архитектура §3, §6.1).

Термины — ровно как в архитектуре §1.4:

* **позиция** — запись КТРУ со статусом ACTIVE;
* **семейство** — корневая позиция (без ``parentPositionInfo``) плюс все её дочерние;
* **лист (конечная позиция)** — позиция без активных дочерних; только её код — итоговый ответ;
* **общая позиция** — корень без характеристик («Мебель прочая») — запасной вариант;
* **услуга** — наименование начинается с «Услуги», в выбор не попадает.

Модуль чистый: ни Django, ни файлов. Загрузка и сборка — в ``kb_build`` и ``infrastructure``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from apps.ste.domain import units as U
from apps.ste.domain.values import AllowedValue, fold_text


def char_key(name: str) -> str:
    """Нормализованное имя характеристики — ключ внутри семейства (архитектура §3.4)."""
    return re.sub(r"\s+", " ", name.casefold().replace("ё", "е")).strip(" .")


@dataclass(frozen=True)
class CharSpec:
    """Характеристика в составе листа или семейства."""

    key: str
    name: str
    #: 1 — качественная, 2 — количественная.
    type: int
    #: 1 — неизменяемая (у листа одно фиксированное значение, определяет лист);
    #: 2 — выбор одного значения; 3 — выбор нескольких.
    kind: int
    required: bool
    allowed: tuple[AllowedValue, ...]

    @property
    def is_numeric(self) -> bool:
        return self.type == 2

    @property
    def unit(self) -> U.Unit | None:
        """Единица характеристики: ОКЕИ первого числового значения или единица из названия."""
        for value in self.allowed:
            if value.is_numeric:
                unit = value.unit
                if unit is not None:
                    return unit
        return U.unit_in_name(self.name)

    @property
    def unit_name(self) -> str | None:
        unit = self.unit
        return unit.name if unit else None

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "type": self.type,
            "kind": self.kind,
            "required": self.required,
            "allowed": [value.to_json() for value in self.allowed],
        }

    @classmethod
    def from_json(cls, data: dict) -> CharSpec:
        return cls(
            key=data["key"],
            name=data["name"],
            type=int(data["type"]),
            kind=int(data["kind"]),
            required=bool(data["required"]),
            allowed=tuple(AllowedValue.from_json(item) for item in data["allowed"]),
        )

    def text_values(self) -> list[str]:
        return [value.text for value in self.allowed if value.kind == "text" and value.text]

    def find_text(self, text: str) -> AllowedValue | None:
        folded = fold_text(text)
        for value in self.allowed:
            if value.kind == "text" and fold_text(value.text or "") == folded:
                return value
        return None


@dataclass(frozen=True)
class Leaf:
    """Конечная позиция КТРУ и её сигнатура (архитектура §3.4)."""

    code: str
    name: str
    family_id: str
    okpd2: str
    chars: dict[str, CharSpec]
    #: Лист — это общая позиция без характеристик.
    is_generic: bool = False

    def to_json(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "family_id": self.family_id,
            "okpd2": self.okpd2,
            "is_generic": self.is_generic,
            "chars": [spec.to_json() for spec in self.chars.values()],
        }

    @classmethod
    def from_json(cls, data: dict) -> Leaf:
        chars = [CharSpec.from_json(item) for item in data["chars"]]
        return cls(
            code=data["code"],
            name=data["name"],
            family_id=data["family_id"],
            okpd2=data["okpd2"],
            is_generic=bool(data.get("is_generic")),
            chars={spec.key: spec for spec in chars},
        )


@dataclass(frozen=True)
class FamilyChar:
    """Характеристика на уровне семейства: объединение по шаблону и всем листьям."""

    key: str
    name: str
    type: int
    kinds: tuple[int, ...]
    required: bool
    allowed: tuple[AllowedValue, ...]
    #: Во скольких листьях семейства характеристика объявлена.
    leaf_count: int
    #: Различает ли листья (набор допустимых значений не одинаков у всех, или есть не у всех).
    differentiator: bool

    @property
    def is_numeric(self) -> bool:
        return self.type == 2

    @property
    def unit(self) -> U.Unit | None:
        for value in self.allowed:
            if value.is_numeric and value.unit is not None:
                return value.unit
        return U.unit_in_name(self.name)

    def text_values(self) -> list[str]:
        return [value.text for value in self.allowed if value.kind == "text" and value.text]

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "type": self.type,
            "kinds": list(self.kinds),
            "required": self.required,
            "allowed": [value.to_json() for value in self.allowed],
            "leaf_count": self.leaf_count,
            "differentiator": self.differentiator,
        }

    @classmethod
    def from_json(cls, data: dict) -> FamilyChar:
        return cls(
            key=data["key"],
            name=data["name"],
            type=int(data["type"]),
            kinds=tuple(int(item) for item in data["kinds"]),
            required=bool(data["required"]),
            allowed=tuple(AllowedValue.from_json(item) for item in data["allowed"]),
            leaf_count=int(data["leaf_count"]),
            differentiator=bool(data["differentiator"]),
        )


@dataclass
class Family:
    """Семейство КТРУ (архитектура §1.4, §3.2)."""

    id: str
    #: Короткий идентификатор для промпта LLM-1: ``F001``.
    short_id: str
    name: str
    okpd2: str
    okpd2_name: str
    #: Категория хакатона: одежда | обувь | сумки | мебель | инструменты.
    category: str
    #: family | generic | service
    role: str
    leaves: list[str]
    chars: dict[str, FamilyChar]
    template_code: str | None = None
    description: str | None = None
    #: Из LLM-метаданных (архитектура §3.7); пусто, пока метаданные не сгенерированы.
    synonyms: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    summary: str | None = None

    @property
    def differentiators(self) -> list[str]:
        return [key for key, char in self.chars.items() if char.differentiator]

    def kind1_values(self, limit: int = 30) -> list[str]:
        """Значения неизменяемых различающих характеристик — «виды» в карточке семейства."""
        seen: dict[str, None] = {}
        for char in self.chars.values():
            if 1 in char.kinds and char.differentiator:
                for value in char.text_values():
                    seen.setdefault(value, None)
        return list(seen)[:limit]

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "short_id": self.short_id,
            "name": self.name,
            "okpd2": self.okpd2,
            "okpd2_name": self.okpd2_name,
            "category": self.category,
            "role": self.role,
            "leaves": self.leaves,
            "template_code": self.template_code,
            "description": self.description,
            "chars": [char.to_json() for char in self.chars.values()],
        }

    @classmethod
    def from_json(cls, data: dict) -> Family:
        chars = [FamilyChar.from_json(item) for item in data["chars"]]
        return cls(
            id=data["id"],
            short_id=data["short_id"],
            name=data["name"],
            okpd2=data["okpd2"],
            okpd2_name=data["okpd2_name"],
            category=data["category"],
            role=data["role"],
            leaves=list(data["leaves"]),
            template_code=data.get("template_code"),
            description=data.get("description"),
            chars={char.key: char for char in chars},
        )


@dataclass
class KnowledgeBase:
    families: dict[str, Family]
    leaves: dict[str, Leaf]
    #: Код любой позиции (в т. ч. шаблона) → id семейства.
    position_family: dict[str, str]
    scope: list[str]
    built_at: str = ""

    def by_short_id(self, short_id: str) -> Family | None:
        if not hasattr(self, "_short"):
            self._short = {family.short_id: family for family in self.families.values()}
        return self._short.get(short_id)

    def selectable(self) -> list[Family]:
        """Конкретные семейства, участвующие в выборе LLM-1 (без услуг и общих позиций)."""
        return [family for family in self.families.values() if family.role == "family"]

    def generic(self) -> list[Family]:
        return [family for family in self.families.values() if family.role == "generic"]

    def leaves_of(self, family_id: str) -> list[Leaf]:
        family = self.families[family_id]
        return [self.leaves[code] for code in family.leaves if code in self.leaves]

    def to_json(self) -> dict:
        return {
            "scope": self.scope,
            "built_at": self.built_at,
            "families": [family.to_json() for family in self.families.values()],
            "leaves": [leaf.to_json() for leaf in self.leaves.values()],
            "position_family": self.position_family,
        }

    @classmethod
    def from_json(cls, data: dict) -> KnowledgeBase:
        families = [Family.from_json(item) for item in data["families"]]
        leaves = [Leaf.from_json(item) for item in data["leaves"]]
        return cls(
            families={family.id: family for family in families},
            leaves={leaf.code: leaf for leaf in leaves},
            position_family=dict(data["position_family"]),
            scope=list(data["scope"]),
            built_at=data.get("built_at", ""),
        )

    def apply_meta(self, meta: dict[str, dict]) -> None:
        """Подмешать LLM-метаданные семейств (синонимы, описание, ключевые слова)."""
        for family_id, item in meta.items():
            family = self.families.get(family_id)
            if family is None:
                continue
            family.synonyms = [str(word) for word in item.get("synonyms", []) if word]
            family.keywords = [str(word) for word in item.get("keywords", []) if word]
            family.summary = item.get("description") or None
