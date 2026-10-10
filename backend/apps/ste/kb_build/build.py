"""Сборка базы знаний из разобранных позиций (архитектура §3.2–3.5).

Роли позиций, семейства, сигнатуры листьев, различающие характеристики. Результат — один
файл ``ktru_kb.json.gz``, который онлайн-часть загружает в память при старте.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from apps.ste.domain import units as U
from apps.ste.domain.kb import CharSpec, Family, FamilyChar, KnowledgeBase, Leaf, char_key
from apps.ste.domain.values import AllowedValue
from apps.ste.kb_build.parse import RawChar, RawPosition, RawValue

#: Охват по умолчанию: одежда, обувь, сумки и рюкзаки, мебель, инструменты.
DEFAULT_SCOPE = ("14.", "15.20", "15.12", "31.", "25.73", "28.24")

#: Префикс ОКПД2 → категория хакатона. Порядок важен: длинный префикс раньше.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("15.20", "обувь"),
    ("15.12", "сумки и рюкзаки"),
    ("14.", "одежда"),
    ("31.", "мебель"),
    ("25.73", "инструменты"),
    ("28.24", "инструменты"),
)

_CATEGORY_ORDER = {"одежда": 0, "обувь": 1, "сумки и рюкзаки": 2, "мебель": 3, "инструменты": 4}

KB_FILE = "ktru_kb.json.gz"


def category_of(okpd2: str) -> str:
    for prefix, name in CATEGORIES:
        if okpd2.startswith(prefix):
            return name
    return "прочее"


def _allowed(raw: RawValue, char: RawChar) -> AllowedValue | None:
    if raw.text is not None and raw.text.strip():
        return AllowedValue(kind="text", text=raw.text.strip())
    okei, unit_name = raw.okei_code, raw.okei_name
    if okei is None:
        # Числовая характеристика без ОКЕИ: единица — в хвосте названия (архитектура §3.3).
        unit = U.unit_in_name(char.name)
        if unit is not None:
            okei, unit_name = unit.code, unit.name
    if raw.concrete is not None:
        return AllowedValue(kind="concrete", concrete=raw.concrete, okei=okei, unit_name=unit_name)
    if raw.minimum is not None or raw.maximum is not None:
        return AllowedValue(
            kind="range",
            minimum=raw.minimum,
            min_inclusive=(raw.min_op or "greaterOrEqual") != "greater",
            maximum=raw.maximum,
            max_inclusive=(raw.max_op or "lessOrEqual") != "less",
            okei=okei,
            unit_name=unit_name,
        )
    return None


def _dedupe(values: list[AllowedValue]) -> tuple[AllowedValue, ...]:
    seen: dict[str, AllowedValue] = {}
    for value in values:
        seen.setdefault(value.key, value)
    return tuple(seen.values())


def leaf_chars(position: RawPosition) -> dict[str, CharSpec]:
    """Сигнатура позиции: актуальные характеристики с допустимыми значениями."""
    merged: dict[str, dict] = {}
    for char in position.chars:
        if not char.actual:
            continue
        key = char_key(char.name)
        values = [value for value in (_allowed(raw, char) for raw in char.values) if value]
        if key in merged:
            merged[key]["allowed"].extend(values)
            merged[key]["required"] = merged[key]["required"] or char.required
            continue
        merged[key] = {
            "name": char.name,
            "type": char.type,
            "kind": char.kind,
            "required": char.required,
            "allowed": values,
        }
    return {
        key: CharSpec(
            key=key,
            name=item["name"],
            type=item["type"],
            kind=item["kind"],
            required=item["required"],
            allowed=_dedupe(item["allowed"]),
        )
        for key, item in merged.items()
    }


def _family_chars(
    template: dict[str, CharSpec] | None, leaves: list[dict[str, CharSpec]]
) -> dict[str, FamilyChar]:
    sources = ([template] if template else []) + leaves
    order: dict[str, None] = {}
    for chars in sources:
        for key in chars:
            order.setdefault(key, None)

    result: dict[str, FamilyChar] = {}
    for key in order:
        specs = [chars[key] for chars in sources if key in chars]
        in_leaves = [chars[key] for chars in leaves if key in chars]
        types = Counter(spec.type for spec in specs)
        allowed: list[AllowedValue] = []
        for spec in specs:
            allowed.extend(spec.allowed)
        signatures = {frozenset(value.key for value in spec.allowed) for spec in in_leaves}
        differentiator = len(leaves) > 1 and (
            len(in_leaves) < len(leaves) or len(signatures) > 1
        )
        result[key] = FamilyChar(
            key=key,
            name=specs[0].name,
            type=types.most_common(1)[0][0],
            kinds=tuple(sorted({spec.kind for spec in specs})),
            required=any(spec.required for spec in specs),
            allowed=_dedupe(allowed),
            leaf_count=len(in_leaves),
            differentiator=differentiator,
        )
    return result


def build(positions: dict[str, RawPosition], scope: tuple[str, ...]) -> KnowledgeBase:
    children: dict[str, list[str]] = defaultdict(list)
    for code, position in positions.items():
        if position.parent and position.parent in positions and position.parent != code:
            children[position.parent].append(code)

    roots = [
        code
        for code, position in positions.items()
        if not position.parent or position.parent not in positions
    ]

    drafts: list[Family] = []
    leaves: dict[str, Leaf] = {}
    position_family: dict[str, str] = {}

    for root_code in roots:
        root = positions[root_code]
        kids = sorted(children.get(root_code, []))
        root_chars = leaf_chars(root)
        if root.name.casefold().startswith("услуг"):
            role = "service"
        elif not kids and not root_chars:
            role = "generic"
        else:
            role = "family"

        leaf_codes = kids or [root_code]
        leaf_signatures: list[dict[str, CharSpec]] = []
        for code in leaf_codes:
            position = positions[code]
            chars = root_chars if code == root_code else leaf_chars(position)
            leaf_signatures.append(chars)
            leaves[code] = Leaf(
                code=code,
                name=position.name,
                family_id=root_code,
                okpd2=position.okpd2,
                chars=chars,
                is_generic=role == "generic",
            )
            position_family[code] = root_code
        position_family[root_code] = root_code

        template = root_chars if kids else None
        drafts.append(
            Family(
                id=root_code,
                short_id="",
                name=root.name,
                okpd2=root.okpd2,
                okpd2_name=root.okpd2_name,
                category=category_of(root.okpd2),
                role=role,
                leaves=leaf_codes,
                chars=_family_chars(template, leaf_signatures),
                template_code=root_code if (kids and root.is_template) else None,
                description=root.description,
            )
        )

    drafts.sort(key=lambda family: (_CATEGORY_ORDER.get(family.category, 9), family.okpd2, family.id))
    for index, family in enumerate(drafts, start=1):
        family.short_id = f"F{index:03d}"

    # Услуги в базе не нужны вовсе: их не выбирают и их коды не выдаются.
    families = {family.id: family for family in drafts if family.role != "service"}
    leaves = {code: leaf for code, leaf in leaves.items() if leaf.family_id in families}
    position_family = {
        code: family_id for code, family_id in position_family.items() if family_id in families
    }

    return KnowledgeBase(
        families=families,
        leaves=leaves,
        position_family=position_family,
        scope=list(scope),
        built_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def save(kb: KnowledgeBase, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / KB_FILE
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(kb.to_json(), stream, ensure_ascii=False, separators=(",", ":"))
    return path


def load(directory: Path) -> KnowledgeBase:
    with gzip.open(directory / KB_FILE, "rt", encoding="utf-8") as stream:
        kb = KnowledgeBase.from_json(json.load(stream))
    meta_path = directory / "family_meta.json"
    if meta_path.exists():
        kb.apply_meta(json.loads(meta_path.read_text(encoding="utf-8")))
    return kb
