"""Сужение конечных позиций по значениям характеристик (архитектура §4.5, §4.6 — конфликт).

Правила §4.5:

* уверенное (``confident``/``confirmed``) значение, несовместимое с листом, **исключает** лист;
* слабое (``weak``) несовместимое значение только штрафует лист (×0,7): оно могло быть
  неверно понято, и выбрасывать из-за него правильный код нельзя;
* характеристики у листа нет — нейтрально, лёгкий штраф (×0,9): лист с этой характеристикой
  описывает товар точнее;
* ``score(лист) = score(семейства) × Π штрафов``.

Когда не осталось ни одного листа, модуль не просто говорит «конфликт», а находит
минимальный набор значений-виновников и ближайшее допустимое значение для каждого — из этого
модуль ошибок строит объяснение «почему значение неверно и на что заменить» (§8.2).

Модуль чистый: без Django и ввода-вывода.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from apps.ste.domain import units as U
from apps.ste.domain.kb import CharSpec, Leaf
from apps.ste.domain.model import Confidence, FunnelStep, NormalizedValue
from apps.ste.domain.normalize import (
    compatible,
    convert_value,
    display_value,
    has_content,
    is_interval_choice,
    split_items,
)
from apps.ste.domain.values import AllowedValue, fold_text, format_number

#: Слабое значение не совпало с листом.
PENALTY_WEAK = 0.7
#: У листа нет характеристики, которую указал пользователь.
PENALTY_MISSING = 0.9

STRONG = (Confidence.CONFIDENT, Confidence.CONFIRMED)


@dataclass(frozen=True)
class RankedLeaf:
    """Оставшийся лист со скором и тем, что его оштрафовало."""

    leaf: Leaf
    score: float
    #: Слабые значения, несовместимые с листом.
    weak_mismatches: tuple[str, ...] = ()
    #: Указанные характеристики, которых у листа нет.
    missing: tuple[str, ...] = ()

    @property
    def code(self) -> str:
        return self.leaf.code


@dataclass
class ConflictCulprit:
    """Значение, из-за которого исключены все листья, и чем его заменить."""

    char_key: str
    char_name: str
    user_value: str
    #: Сколько листьев это значение исключает само по себе.
    excludes_alone: int
    #: Сколько листьев вернётся, если исправить набор виновников, куда входит это значение.
    restores: int
    #: Допустимые значения у возвращаемых листьев (описания КТРУ).
    allowed: list[str] = field(default_factory=list)
    #: Ближайшее допустимое: интервал для числа, похожий текст для текста.
    nearest: str | None = None
    #: Другие значения, которые нужно менять вместе с этим (набор из нескольких виновников).
    together_with: list[str] = field(default_factory=list)


@dataclass
class Conflict:
    """Почему не осталось ни одного листа (архитектура §4.6, строка «0 листьев»)."""

    #: char_key → сколько листьев исключает это уверенное значение само по себе.
    excludes_alone: dict[str, int]
    #: Минимальные наборы значений, после исправления которых вернётся хотя бы один лист.
    culprit_sets: list[tuple[str, ...]]
    #: Виновники по убыванию подозрительности: выведенные из текста раньше подтверждённых
    #: пользователем, не kind=1 раньше kind=1, затем — кто сам исключает больше листьев.
    culprits: list[ConflictCulprit]


@dataclass
class NarrowingOutcome:
    #: Оставшиеся листья: скор по убыванию, затем код.
    remaining: list[RankedLeaf]
    #: Исключённые листья: код → уверенные значения (char_key), с которыми лист несовместим.
    excluded: dict[str, list[str]]
    #: Как сокращалось число листьев, если применять уверенные значения по одному
    #: (сначала kind=1). ``level`` — характеристика, ``label`` — значение, ``count`` — остаток.
    steps: list[FunnelStep]
    conflict: Conflict | None
    #: Сколько листьев было до сужения.
    total: int

    @property
    def leaves(self) -> list[Leaf]:
        return [item.leaf for item in self.remaining]

    @property
    def codes(self) -> list[str]:
        return [item.code for item in self.remaining]

    @property
    def count(self) -> int:
        return len(self.remaining)


def _is_kind1(key: str, leaves: Iterable[Leaf]) -> bool:
    return any(leaf.chars[key].kind == 1 for leaf in leaves if key in leaf.chars)


def narrow(
    leaves: Sequence[Leaf],
    values: dict[str, NormalizedValue],
    family_scores: dict[str, float] | None = None,
) -> NarrowingOutcome:
    """Сузить листья по значениям (архитектура §4.5).

    ``values`` — значения одного семейства по ``char_key``; значения без содержимого
    (число не распознано) не участвуют. ``family_scores`` — уверенность в семействе от LLM-1;
    по умолчанию 1.
    """
    usable = {key: nv for key, nv in values.items() if has_content(nv)}
    strong = [key for key, nv in usable.items() if nv.confidence in STRONG]

    remaining: list[RankedLeaf] = []
    excluded: dict[str, list[str]] = {}
    for leaf in leaves:
        bad: list[str] = []
        weak: list[str] = []
        missing: list[str] = []
        for key, nv in usable.items():
            verdict = compatible(leaf.chars.get(key), nv)
            if verdict is None:
                missing.append(key)
            elif not verdict:
                (bad if nv.confidence in STRONG else weak).append(key)
        if bad:
            excluded[leaf.code] = bad
            continue
        base = (family_scores or {}).get(leaf.family_id, 1.0)
        score = base * PENALTY_WEAK ** len(weak) * PENALTY_MISSING ** len(missing)
        remaining.append(RankedLeaf(leaf, score, tuple(weak), tuple(missing)))
    remaining.sort(key=lambda item: (-item.score, item.code))

    steps = _steps(leaves, usable, strong, excluded)
    conflict = None
    if not remaining and leaves and strong:
        conflict = _conflict(leaves, usable, excluded)
    return NarrowingOutcome(
        remaining=remaining,
        excluded=excluded,
        steps=steps,
        conflict=conflict,
        total=len(leaves),
    )


def _steps(
    leaves: Sequence[Leaf],
    values: dict[str, NormalizedValue],
    strong: list[str],
    excluded: dict[str, list[str]],
) -> list[FunnelStep]:
    """Воронка внутри семейства: сначала неизменяемые (kind=1) — они и определяют лист."""
    order = sorted(strong, key=lambda key: 0 if _is_kind1(key, leaves) else 1)
    applied: set[str] = set()
    steps = []
    for key in order:
        applied.add(key)
        count = sum(1 for leaf in leaves if not applied & set(excluded.get(leaf.code, ())))
        nv = values[key]
        steps.append(FunnelStep(level=nv.char_name, label=display_value(nv), count=count))
    return steps


# --- конфликт --------------------------------------------------------------------------------


def _conflict(
    leaves: Sequence[Leaf],
    values: dict[str, NormalizedValue],
    excluded: dict[str, list[str]],
) -> Conflict:
    """Минимальные наборы виновников и подсказки для исправления.

    Лист вернётся, если исправить все значения, которые его исключили. Поэтому минимальные
    наборы виновников — это самые короткие из наборов «исключившие значения листа».
    """
    excludes_alone = {key: 0 for key, nv in values.items() if nv.confidence in STRONG}
    for keys in excluded.values():
        for key in keys:
            excludes_alone[key] += 1

    by_set: dict[frozenset[str], list[Leaf]] = {}
    for leaf in leaves:
        by_set.setdefault(frozenset(excluded[leaf.code]), []).append(leaf)
    smallest = min(len(keys) for keys in by_set)
    order = list(values)
    culprit_sets = sorted(
        (tuple(sorted(keys, key=order.index)) for keys in by_set if len(keys) == smallest),
        key=lambda keys: [order.index(key) for key in keys],
    )

    culprits: list[ConflictCulprit] = []
    for key in dict.fromkeys(key for keys in culprit_sets for key in keys):
        restored = [
            leaf
            for keys, group in by_set.items()
            if len(keys) == smallest and key in keys
            for leaf in group
        ]
        together = sorted(
            {other for keys in culprit_sets if key in keys for other in keys if other != key},
            key=order.index,
        )
        specs = [leaf.chars[key] for leaf in restored if key in leaf.chars]
        nv = values[key]
        culprits.append(
            ConflictCulprit(
                char_key=key,
                char_name=nv.char_name,
                user_value=_user_value(nv),
                excludes_alone=excludes_alone.get(key, 0),
                restores=len(restored),
                allowed=allowed_descriptions(specs),
                nearest=nearest_allowed(specs, nv),
                together_with=together,
            )
        )
    # Подозрительнее всего значение из текста (не подтверждённое пользователем) и не kind=1:
    # «что это за товар» пользователь путает реже, чем второстепенную характеристику.
    culprits.sort(
        key=lambda item: (
            1 if values[item.char_key].confidence == Confidence.CONFIRMED else 0,
            1 if _is_kind1(item.char_key, leaves) else 0,
            -item.excludes_alone,
        )
    )
    return Conflict(excludes_alone=excludes_alone, culprit_sets=culprit_sets, culprits=culprits)


def _user_value(nv: NormalizedValue) -> str:
    if nv.value_type == "number" and nv.number is not None and not is_interval_choice(nv):
        return f"{format_number(nv.number)} {nv.user_unit or nv.ktru_unit or ''}".strip()
    return nv.user_text or ", ".join(nv.texts)


def _numeric_order(value: AllowedValue) -> float:
    """Нижняя граница значения; открытый снизу интервал («<300») — раньше всех."""
    if value.kind == "concrete":
        return value.concrete or 0.0
    return value.minimum if value.minimum is not None else float("-inf")


def allowed_values(specs: Iterable[CharSpec]) -> list[AllowedValue]:
    """Объединение допустимых значений без повторов; числа — по возрастанию."""
    seen: dict[str, AllowedValue] = {}
    for spec in specs:
        for value in spec.allowed:
            seen.setdefault(value.key, value)
    items = list(seen.values())
    if items and all(value.is_numeric for value in items):
        items.sort(key=_numeric_order)
    return items


def allowed_descriptions(specs: Iterable[CharSpec]) -> list[str]:
    return [value.describe() for value in allowed_values(specs)]


def nearest_allowed(specs: Iterable[CharSpec], nv: NormalizedValue) -> str | None:
    """Ближайшее допустимое значение — то, на что предложить заменить ошибочное.

    Число сравнивается в единице каждого значения КТРУ, а расстояние — в базовой единице
    размерности, чтобы сантиметры и миллиметры не спорили. Для текста — самый похожий
    по написанию допустимый текст. Единица другой размерности: ближайшего нет.
    """
    values = allowed_values(specs)
    if nv.value_type == "number":
        if nv.number is None or not nv.unit_ok:
            return None
        number = nv.number_in_ktru_unit if nv.number_in_ktru_unit is not None else nv.number
        unit = U.parse_unit(nv.ktru_unit) if nv.ktru_unit else None
        best: tuple[float, AllowedValue] | None = None
        for value in values:
            if not value.is_numeric:
                continue
            target = value.unit or unit
            converted = convert_value(number, unit, target)
            if converted is None:
                continue
            distance = value.distance(converted) * (target.factor if target else 1.0)
            if best is None or distance < best[0]:
                best = (distance, value)
        return best[1].describe() if best else None

    texts = [value.text for value in values if value.kind == "text" and value.text]
    if not texts:
        return None
    allowed = {fold_text(text) for text in texts}
    wrong = [text for text in nv.texts if fold_text(text) not in allowed]
    probe = wrong[0] if wrong else (split_items(nv.user_text, multi=True) or [""])[0]
    probe = fold_text(probe)
    return max(texts, key=lambda text: SequenceMatcher(None, probe, fold_text(text)).ratio())


# --- чем различаются листья ---------------------------------------------------------------------

#: Сколько значений списка показывать подряд, прежде чем свернуть в «и ещё N».
_SHOWN_VALUES = 3


def summarize_spec(spec: CharSpec | None) -> str:
    """Коротко, что допускает лист: «Куртка», «≥500 <3200 Ватт (27 интервалов)», «—»."""
    if spec is None:
        return "—"
    values = allowed_values([spec])
    if not values:
        return "—"
    if all(value.is_numeric for value in values) and len(values) > _SHOWN_VALUES:
        # Длинный ряд интервалов (27 диапазонов мощности) — крайние значения и их число.
        return f"{values[0].describe()} … {values[-1].describe()} ({len(values)} знач.)"
    shown = [value.describe() for value in values[:_SHOWN_VALUES]]
    more = len(values) - len(shown)
    return ", ".join(shown) + (f" и ещё {more}" if more > 0 else "")


def _signature(spec: CharSpec | None) -> frozenset[str] | None:
    return None if spec is None else frozenset(value.key for value in spec.allowed)


def distinguishing_keys(leaves: Sequence[Leaf]) -> list[str]:
    """Характеристики, по которым листья различаются (у части листьев её нет — тоже различие)."""
    order: dict[str, None] = {}
    for leaf in leaves:
        for key in leaf.chars:
            order.setdefault(key, None)
    return [key for key in order if len({_signature(leaf.chars.get(key)) for leaf in leaves}) > 1]


def leaf_distinctions(
    leaves: Sequence[Leaf], limit_chars: int = 3
) -> dict[str, list[tuple[str, str]]]:
    """Чем различаются листья: код → [(характеристика, что допускает этот лист)].

    Характеристики, в которых все листья согласны, пропускаются. Порядок — сначала
    неизменяемые (kind=1, они и задают лист), затем те, что разбивают листья на больше групп.
    """
    keys = distinguishing_keys(leaves)
    position = {key: index for index, key in enumerate(keys)}

    def rank(key: str) -> tuple[int, int, int, int]:
        groups = len({_signature(leaf.chars.get(key)) for leaf in leaves})
        required = any(leaf.chars[key].required for leaf in leaves if key in leaf.chars)
        return (0 if _is_kind1(key, leaves) else 1, -groups, 0 if required else 1, position[key])

    chosen = sorted(keys, key=rank)[: max(limit_chars, 0)]
    result: dict[str, list[tuple[str, str]]] = {}
    for leaf in leaves:
        rows = []
        for key in chosen:
            spec = leaf.chars.get(key)
            name = spec.name if spec else _name_of(key, leaves)
            rows.append((name, summarize_spec(spec)))
        result[leaf.code] = rows
    return result


def _name_of(key: str, leaves: Sequence[Leaf]) -> str:
    return next((leaf.chars[key].name for leaf in leaves if key in leaf.chars), key)
