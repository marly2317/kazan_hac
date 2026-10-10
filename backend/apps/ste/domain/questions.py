"""Уточняющий вопрос и дозаполнение (архитектура §4.6).

Вопрос задаётся по характеристике, ответ на которую в среднем оставит меньше всего листьев:
для каждой характеристики ``c``, по которой оставшиеся листья различаются,

* варианты ``V(c)`` — объединение значений ``c`` у листьев (тексты или интервалы),
* ``L_v`` — листья, совместимые с вариантом ``v`` (лист без ``c`` остаётся — как в сужении),
* ``P(v) ∝ |L_v|``, ожидаемый остаток ``E(c) = Σ P(v)·|L_v|``.

Берётся ``c`` с минимальным ``E(c)``; при равенстве — kind=1, затем обязательная, затем
с меньшим числом вариантов. Совместимость считается той же функцией, что и сужение, поэтому
число «останется N» на кнопке совпадает с тем, что пользователь увидит после ответа.

Модуль чистый: без Django и ввода-вывода.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from apps.ste.domain.kb import CharSpec, Leaf
from apps.ste.domain.model import NormalizedValue, Question, QuestionOption, Source
from apps.ste.domain.narrowing import RankedLeaf, leaf_distinctions
from apps.ste.domain.normalize import compatible, has_content, value_from_allowed
from apps.ste.domain.values import AllowedValue, fold_text

#: Значение кнопки «Не знаю» (контракт API: ``answer`` с ``value="__unknown__"``).
UNKNOWN_VALUE = "__unknown__"
UNKNOWN_LABEL = "Не знаю"
#: Сколько вариантов показывать в списке «дозаполнить».
FILL_OPTIONS_LIMIT = 30


def _plural(count: int, forms: tuple[str, str, str]) -> str:
    """1 вариант, 2 варианта, 5 вариантов."""
    tail = count % 100
    if 11 <= tail <= 14:
        return forms[2]
    if count % 10 == 1:
        return forms[0]
    if 2 <= count % 10 <= 4:
        return forms[1]
    return forms[2]


def remaining_phrase(count: int) -> str:
    """«остался 21 вариант», «осталось 8 вариантов»."""
    verb = "остался" if count % 10 == 1 and count % 100 != 11 else "осталось"
    return f"{verb} {count} {_plural(count, ('вариант', 'варианта', 'вариантов'))}"


def _as_leaves(leaves: Sequence[Leaf | RankedLeaf]) -> list[Leaf]:
    return [item.leaf if isinstance(item, RankedLeaf) else item for item in leaves]


@dataclass
class _Candidate:
    key: str
    name: str
    expected: float
    kind1: bool
    required: bool
    options: list[QuestionOption]
    order: int

    def rank(self) -> tuple:
        return (
            round(self.expected, 9),
            0 if self.kind1 else 1,
            0 if self.required else 1,
            len(self.options),
            self.order,
        )


def _option_key(value: AllowedValue) -> str:
    """Ключ варианта: тексты сравниваются без регистра/ё, интервалы — точно."""
    return "t:" + fold_text(value.text or "") if value.kind == "text" else value.key


def _option_sort_key(value: AllowedValue) -> tuple:
    """При равном остатке: тексты по алфавиту, числа по возрастанию."""
    if value.kind == "concrete":
        return (value.concrete or 0.0, "")
    if value.kind == "range":
        return (value.minimum if value.minimum is not None else float("-inf"), "")
    return (0.0, fold_text(value.text or ""))


def _evaluate(key: str, leaves: list[Leaf], order: int) -> _Candidate | None:
    specs = [leaf.chars.get(key) for leaf in leaves]
    present = [spec for spec in specs if spec is not None]
    keysets = [None if spec is None else {_option_key(v) for v in spec.allowed} for spec in specs]
    if not present or len({None if ks is None else frozenset(ks) for ks in keysets}) < 2:
        return None

    options: dict[str, AllowedValue] = {}
    for spec in present:
        for value in spec.allowed:
            options.setdefault(_option_key(value), value)

    reference = present[0]
    total = len(leaves)
    scored: list[tuple[QuestionOption, AllowedValue]] = []
    for option_key, value in options.items():
        nv: NormalizedValue | None = None
        count = 0
        for spec, keyset in zip(specs, keysets, strict=True):
            if keyset is None or option_key in keyset:
                count += 1
            elif value.is_numeric:
                # Интервал другого листа может пересекаться с вариантом — проверка числом,
                # ровно как при сужении после ответа кнопкой.
                nv = nv or value_from_allowed(reference, value)
                count += compatible(spec, nv) is not False
        label = value.describe()
        scored.append((QuestionOption(label=label, value=label, remaining=count), value))
    if all(option.remaining >= total for option, _ in scored):
        # Ни один ответ не сокращает список — спрашивать бессмысленно.
        return None

    sizes = [option.remaining for option, _ in scored]
    expected = sum(size * size for size in sizes) / sum(sizes)
    scored.sort(key=lambda item: (-item[0].remaining, *_option_sort_key(item[1])))
    return _Candidate(
        key=key,
        name=reference.name,
        expected=expected,
        kind1=any(spec.kind == 1 for spec in present),
        required=any(spec.required for spec in present),
        options=[option for option, _ in scored],
        order=order,
    )


def choose_question(
    leaves: Sequence[Leaf | RankedLeaf],
    known_keys: set[str],
    skipped: set[str],
    include_unknown: bool = False,
) -> Question | None:
    """Самый информативный вопрос по характеристике или ``None``.

    ``known_keys`` — характеристики с уверенным/подтверждённым значением (их не спрашиваем),
    ``skipped`` — на которые пользователь ответил «Не знаю». ``None`` — листьев меньше двух
    или ни одна характеристика их не разделяет (тогда :func:`leaf_question`).
    ``include_unknown`` добавляет в конец кнопку «Не знаю» (``value="__unknown__"``).
    """
    items = _as_leaves(leaves)
    if len(items) < 2:
        return None
    order: dict[str, None] = {}
    for leaf in items:
        for key in leaf.chars:
            order.setdefault(key, None)

    best: _Candidate | None = None
    for index, key in enumerate(order):
        if key in known_keys or key in skipped:
            continue
        candidate = _evaluate(key, items, index)
        if candidate is not None and (best is None or candidate.rank() < best.rank()):
            best = candidate
    if best is None:
        return None

    options = list(best.options)
    if include_unknown:
        options.append(QuestionOption(UNKNOWN_LABEL, UNKNOWN_VALUE, len(items)))
    return Question(
        char_key=best.key,
        title=f"Уточните: {best.name}",
        why=f"от этого зависит итоговый код ({remaining_phrase(len(items))})",
        options=options,
        allow_free_text=True,
        kind="char",
    )


def family_question(
    cands: Sequence[tuple[str, str, float]],
    leaf_counts: dict[str, int] | None = None,
) -> Question:
    """«Уточните вид товара»: подходят несколько семейств КТРУ (архитектура §4.6).

    ``cands`` — (family_id, название, score); варианты — по убыванию score. ``remaining``
    варианта — число листьев семейства из ``leaf_counts`` (0, если не передано).
    """
    ordered = sorted(cands, key=lambda item: -item[2])
    options = [
        QuestionOption(label=name, value=family_id, remaining=(leaf_counts or {}).get(family_id, 0))
        for family_id, name, _score in ordered
    ]
    return Question(
        char_key=None,
        title="Уточните вид товара",
        why=(
            f"описанию соответствуют {len(options)} "
            f"{_plural(len(options), ('тип', 'типа', 'типов'))} товара КТРУ — "
            "от выбора зависят характеристики и итоговый код"
        ),
        options=options,
        allow_free_text=True,
        kind="family",
    )


def leaf_question(leaves: Sequence[Leaf | RankedLeaf]) -> Question:
    """Выбор кода вручную: характеристики не различают оставшиеся листья (§4.6)."""
    items = _as_leaves(leaves)
    distinctions = leaf_distinctions(items)
    options = []
    for leaf in items:
        rows = distinctions.get(leaf.code) or []
        details = "; ".join(f"{name}: {value}" for name, value in rows)
        label = f"{leaf.code} — {leaf.name}" + (f" ({details})" if details else "")
        options.append(QuestionOption(label=label, value=leaf.code, remaining=1))
    return Question(
        char_key=None,
        title="Выберите позицию КТРУ",
        why=(
            f"характеристики не различают оставшиеся позиции ({len(items)}) — "
            "выберите код из списка"
        ),
        options=options,
        allow_free_text=False,
        kind="leaf",
    )


# --- дозаполнение после выбора кода ---------------------------------------------------------------


@dataclass
class FillItem:
    """Характеристика, которую нужно (или можно) заполнить, и её допустимые значения."""

    key: str
    name: str
    options: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"key": self.key, "name": self.name, "options": list(self.options)}


@dataclass
class FillResult:
    #: Заполнено по КТРУ: у листа единственное допустимое значение (kind=1 — всегда).
    by_ktru: dict[str, NormalizedValue] = field(default_factory=dict)
    #: Обязательные, не указанные пользователем: «Нужно дозаполнить».
    to_fill_required: list[FillItem] = field(default_factory=list)
    #: Необязательные, не указанные: «Можно заполнить» (не навязываем).
    to_fill_optional: list[FillItem] = field(default_factory=list)


def _options(spec: CharSpec) -> list[str]:
    seen: dict[str, None] = {}
    for value in spec.allowed:
        seen.setdefault(value.describe(), None)
    return list(seen)[:FILL_OPTIONS_LIMIT]


def fill_lists(leaf: Leaf, values: dict[str, NormalizedValue]) -> FillResult:
    """Что делать с характеристиками итогового листа, которых нет в тексте (§4.6).

    Единственное допустимое значение (у kind=1 оно по определению одно) берётся из КТРУ;
    остальное — в списки «дозаполнить» (обязательные) и «можно заполнить».
    """
    result = FillResult()
    for key, spec in leaf.chars.items():
        nv = values.get(key)
        if nv is not None and has_content(nv):
            continue
        if len(spec.allowed) == 1:
            result.by_ktru[key] = value_from_allowed(spec, spec.allowed[0], Source.KTRU)
            continue
        item = FillItem(key=key, name=spec.name, options=_options(spec))
        (result.to_fill_required if spec.required else result.to_fill_optional).append(item)
    return result
