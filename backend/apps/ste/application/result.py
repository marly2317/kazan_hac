"""Сборка ответа ``Result`` (архитектура §6.3) из состояния сессии — детерминированно.

Всё, что здесь происходит, — код без LLM: сужение листьев (§4.5), решение и вопрос (§4.6),
дозаполнение и карточка (§4.6–4.7), сигналы ошибок (§8.2) и воронка для интерфейса.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from apps.ste.domain import narrowing as N
from apps.ste.domain import questions as Q
from apps.ste.domain import signals as S
from apps.ste.domain.card import build_card
from apps.ste.domain.kb import Family, KnowledgeBase, Leaf
from apps.ste.domain.model import (
    Confidence,
    Severity,
    Signal,
    SignalType,
    Stage,
    Status,
    TypoFix,
)

if TYPE_CHECKING:
    from apps.ste.application.pipeline import State

#: Сколько оставшихся листьев показывать списком.
SHOWN_LEAVES = 10

_STRONG = {Confidence.CONFIDENT, Confidence.CONFIRMED}


def _leaf(item) -> Leaf:
    """Элемент ``remaining`` — лист или обёртка над ним (лист + счёт)."""
    if isinstance(item, Leaf):
        return item
    if isinstance(item, tuple):
        return item[0]
    return item.leaf


def _score(item) -> float:
    if isinstance(item, tuple) and len(item) > 1:
        return float(item[1])
    return float(getattr(item, "score", 1.0))


class ResultBuilder:
    def __init__(self, kb: KnowledgeBase, state: State) -> None:
        self.kb = kb
        self.state = state

    # --- общие части ----------------------------------------------------------------------

    def _base(self) -> dict:
        state = self.state
        return {
            "stage": Stage.PREVIEW.value,
            "message": "",
            "text": {"original": state.original, "corrected": state.text, "fixes": state.fixes},
            "preview": state.preview,
            "funnel": [],
            "family": None,
            "family_candidates": [],
            "leaves_count": 0,
            "leaves": [],
            "question": None,
            "final_code": None,
            "card": None,
            "to_fill_required": [],
            "to_fill_optional": [],
            "signals": [],
        }

    def _typo_signals(self) -> list[Signal]:
        return [S.typo_signal(TypoFix(**fix)) for fix in self.state.fixes]

    def _catalog_step(self) -> dict:
        return {
            "level": "Каталог",
            "label": "Конкретные семейства КТРУ: одежда, обувь, сумки, мебель, инструменты",
            "count": len(self.kb.selectable()),
        }

    def _family_json(self, family: Family, score: float, reason: str) -> dict:
        return {
            "id": family.id,
            "short_id": family.short_id,
            "name": family.name,
            "okpd2": family.okpd2,
            "okpd2_name": family.okpd2_name,
            "category": family.category,
            "reason": reason,
            "score": round(score, 3),
        }

    def _note_signals(self) -> list[Signal]:
        note = self.state.llm_note
        if not note:
            return []
        return [Signal(type=SignalType.NOTICE, severity=Severity.INFO, message=note)]

    # --- сборка ---------------------------------------------------------------------------

    def build(self) -> dict:
        state = self.state
        result = self._base()
        signals: list[Signal] = self._typo_signals() + self._note_signals()

        if state.selection.get("not_found") or not state.candidates:
            return self._not_found(result, signals)

        families = []
        for choice in state.candidates:
            family = self.kb.families.get(choice["family_id"])
            if family is not None:
                families.append((family, float(choice.get("score", 1.0)), choice.get("reason", "")))
        if not families:
            return self._not_found(result, signals)

        result["family_candidates"] = [
            {"id": family.id, "name": family.name, "score": round(score, 3), "reason": reason}
            for family, score, reason in families
        ]

        outcomes = []
        for family, score, reason in families:
            values = self._values(family.id)
            leaves = self.kb.leaves_of(family.id)
            outcome = N.narrow(leaves, values, {family.id: score})
            outcomes.append((family, score, reason, values, leaves, outcome))

        if state.chosen_leaf:
            chosen = self.kb.leaves.get(state.chosen_leaf)
            focus = next(
                (item for item in outcomes if chosen and item[0].id == chosen.family_id), None
            )
            if focus is not None and chosen is not None:
                return self._done(result, signals, focus, chosen, chosen_by_user=True)

        alive = [item for item in outcomes if item[5].remaining]
        if len(outcomes) > 1 and len(alive) > 1:
            return self._family_question(result, signals, alive)
        focus = alive[0] if alive else outcomes[0]
        family, score, reason, values, leaves, outcome = focus
        remaining = [_leaf(item) for item in outcome.remaining]

        result["family"] = self._family_json(family, score, reason)
        signals += self._value_signals(family)

        if family.role == "generic" and remaining:
            return self._generic(result, signals, focus, remaining[0])
        if not remaining:
            return self._conflict(result, signals, focus)
        if len(remaining) == 1:
            return self._done(result, signals, focus, remaining[0])
        return self._question(result, signals, focus, remaining)

    # --- исходы ---------------------------------------------------------------------------

    def _not_found(self, result: dict, signals: list[Signal]) -> dict:
        suggestions = [hit["name"] for hit in self.state.preview[:3]]
        signals.append(S.not_found_signal(self.state.text, suggestions))
        result["stage"] = Stage.NOT_FOUND.value
        reason = self.state.selection.get("reason") or "тип товара не определён"
        result["message"] = (
            "Не удалось определить тип товара по КТРУ ("
            + reason.rstrip(".")
            + "). Уточните, что это за товар, и укажите основные характеристики."
        )
        result["funnel"] = [
            self._catalog_step(),
            {"level": "Семейство", "label": "не определено", "count": 0},
        ]
        result["signals"] = self._signals_json(signals)
        return result

    def _family_question(self, result: dict, signals: list[Signal], alive: list) -> dict:
        question = Q.family_question(
            [(item[0].id, item[0].name, item[1]) for item in alive],
            {item[0].id: len(item[5].remaining) for item in alive},
        )
        result["stage"] = Stage.QUESTION.value
        result["question"] = self._question_json(question)
        result["leaves_count"] = sum(len(item[5].remaining) for item in alive)
        result["message"] = "Подходят несколько типов товара КТРУ — уточните, какой именно."
        result["funnel"] = [
            self._catalog_step(),
            {"level": "Семейство", "label": "несколько кандидатов", "count": len(alive)},
            {
                "level": "Конечные позиции",
                "label": "во всех кандидатах",
                "count": result["leaves_count"],
            },
        ]
        result["signals"] = self._signals_json(signals)
        return result

    def _question(self, result: dict, signals: list[Signal], focus, remaining: list[Leaf]) -> dict:
        family, score, reason, values, leaves, outcome = focus
        # Спрашиваем и о том, что распознано неуверенно: слабое значение подтверждается ответом.
        known = {key for key, nv in values.items() if nv.confidence in _STRONG}
        question = Q.choose_question(remaining, known, set(self.state.skipped))
        if question is None:
            question = Q.leaf_question(remaining)
        result["stage"] = Stage.QUESTION.value
        result["question"] = self._question_json(question)
        result["leaves_count"] = len(remaining)
        result["leaves"] = self._leaves_json(outcome, remaining)
        result["funnel"] = self._funnel(family, leaves, outcome, None)
        result["card"] = build_card(
            family, None, values, None, self._extra(family.id), self.state.brand, self.state.model
        )
        result["message"] = (
            f"Тип товара: «{family.name}». В КТРУ {Q.remaining_phrase(len(remaining))} — "
            f"{question.title[:1].lower()}{question.title[1:].rstrip('.')}."
        )
        result["signals"] = self._signals_json(signals)
        return result

    def _done(
        self, result: dict, signals: list[Signal], focus, leaf: Leaf, chosen_by_user: bool = False
    ) -> dict:
        family, score, reason, values, leaves, outcome = focus
        result["family"] = result["family"] or self._family_json(family, score, reason)
        fill = Q.fill_lists(leaf, values)
        result["stage"] = Stage.DONE.value
        result["final_code"] = leaf.code
        result["leaves_count"] = 1
        result["leaves"] = [{"code": leaf.code, "name": leaf.name, "score": 1.0, "distinct": []}]
        result["card"] = build_card(
            family, leaf, values, fill, self._extra(family.id), self.state.brand, self.state.model
        )
        result["to_fill_required"] = [self._fill_json(item) for item in fill.to_fill_required]
        result["to_fill_optional"] = [self._fill_json(item) for item in fill.to_fill_optional]
        missing = [
            (item["key"], item["name"], item.get("options", []))
            for item in result["to_fill_required"]
        ]
        signals += S.missing_required_signals(missing, leaf.code)
        result["funnel"] = self._funnel(family, leaves, outcome, leaf)
        message = f"Код КТРУ определён: {leaf.code} — {leaf.name}."
        if chosen_by_user:
            message = f"Выбрана позиция КТРУ: {leaf.code} — {leaf.name}."
        if missing:
            names = ", ".join(name for _, name, _ in missing[:4])
            message += f" Дозаполните обязательные характеристики: {names}."
        result["message"] = message
        result["signals"] = self._signals_json(signals)
        return result

    def _generic(self, result: dict, signals: list[Signal], focus, leaf: Leaf) -> dict:
        result = self._done(result, signals, focus, leaf)
        result["message"] = (
            f"Конкретной позиции КТРУ для этого товара нет — предварительно общая позиция "
            f"{leaf.code} «{leaf.name}». Характеристики указаны как дополнительные."
        )
        result["signals"].insert(
            0,
            Signal(
                type=SignalType.NOT_FOUND,
                severity=Severity.WARNING,
                message="Конкретной позиции КТРУ нет, выбрана общая позиция без характеристик.",
                suggestion="Уточните вид товара — возможно, подойдёт конкретное семейство.",
                family_id=leaf.family_id,
                leaf_code=leaf.code,
            ).to_json(),
        )
        return result

    def _conflict(self, result: dict, signals: list[Signal], focus) -> dict:
        family, score, reason, values, leaves, outcome = focus
        facts = self._conflict_facts(outcome)
        if facts:
            signals.append(S.conflict_signal(facts, family.name))
        result["stage"] = Stage.CONFLICT.value
        result["leaves_count"] = 0
        result["funnel"] = self._funnel(family, leaves, outcome, None)
        result["card"] = build_card(
            family, None, values, None, self._extra(family.id), self.state.brand, self.state.model
        )
        culprits = ", ".join(f"«{fact.char_name}»" for fact in facts) or "указанные значения"
        result["message"] = (
            f"Тип товара: «{family.name}», но {culprits} не подходит ни к одной конечной позиции "
            "КТРУ — исправьте значение (подсказка — в списке ошибок)."
        )
        result["signals"] = self._signals_json(signals)
        return result

    # --- части ответа ---------------------------------------------------------------------

    def _values(self, family_id: str) -> dict:
        from apps.ste.application.pipeline import _nv_from

        return {key: _nv_from(data) for key, data in self.state.values.get(family_id, {}).items()}

    def _extra(self, family_id: str) -> list[dict]:
        return list(self.state.extra.get(family_id, []))

    def _value_signals(self, family: Family) -> list[Signal]:
        from apps.ste.application.pipeline import _ev_from

        values = self._values(family.id)
        extracted = [_ev_from(data) for data in self.state.extracted.get(family.id, {}).values()]
        out: list[Signal] = []
        for ev in extracted:
            if ev.status == Status.ABSENT:
                continue
            spec = family.chars.get(ev.char_key)
            nv = values.get(ev.char_key)
            if spec is None or nv is None:
                continue
            out += S.value_signals(spec, ev, nv, family.id)
        names = {key: char.name for key, char in family.chars.items()}
        dupes = [_ev_from(data) for data in self.state.duplicates.get(family.id, [])]
        if dupes:
            out += S.duplicate_signals(extracted + dupes, names)
        return out

    def _conflict_facts(self, outcome) -> list:
        conflict = getattr(outcome, "conflict", None)
        if conflict is None:
            return []
        facts = getattr(conflict, "facts", None)
        if facts is not None:
            return list(facts)
        culprits = getattr(conflict, "culprits", None) or []
        result = []
        for item in culprits:
            result.append(
                S.ConflictFact(
                    char_key=item.char_key,
                    char_name=item.char_name,
                    user_value=str(item.user_value),
                    excludes=int(item.excludes_alone),
                    nearest_allowed=item.nearest,
                )
            )
        return result

    def _leaves_json(self, outcome, remaining: list[Leaf]) -> list[dict]:
        shown = remaining[:SHOWN_LEAVES]
        distinctions = N.leaf_distinctions(shown)
        scores = {_leaf(item).code: _score(item) for item in outcome.remaining}
        return [
            {
                "code": leaf.code,
                "name": leaf.name,
                "score": round(scores.get(leaf.code, 1.0), 3),
                "distinct": [
                    {"char": char, "value": value}
                    for char, value in distinctions.get(leaf.code, [])
                ],
            }
            for leaf in shown
        ]

    def _funnel(self, family: Family, leaves: list[Leaf], outcome, leaf: Leaf | None) -> list[dict]:
        in_group = sum(1 for item in self.kb.selectable() if item.okpd2 == family.okpd2)
        funnel = [
            self._catalog_step(),
            {
                "level": "ОКПД2",
                "label": f"{family.okpd2} {family.okpd2_name}",
                "count": max(in_group, 1),
            },
            {"level": "Семейство", "label": family.name, "count": 1},
            {"level": "Конечные позиции", "label": "в семействе", "count": len(leaves)},
        ]
        for step in getattr(outcome, "steps", []) or []:
            funnel.append(self._step_json(step))
        if leaf is not None:
            funnel.append({"level": "Итог", "label": leaf.code, "count": 1})
        return funnel

    @staticmethod
    def _step_json(step) -> dict:
        if isinstance(step, dict):
            return {
                "level": step.get("level", "Характеристика"),
                "label": step.get("label", ""),
                "count": int(step.get("count", step.get("after", 0))),
            }
        return {
            "level": getattr(step, "level", "Характеристика"),
            "label": getattr(step, "label", ""),
            "count": int(getattr(step, "count", getattr(step, "after", 0))),
        }

    @staticmethod
    def _question_json(question) -> dict:
        return {
            "kind": question.kind,
            "char_key": question.char_key,
            "title": question.title,
            "why": question.why,
            "options": [
                {"label": option.label, "value": option.value, "remaining": option.remaining}
                for option in question.options
            ],
            "allow_free_text": question.allow_free_text,
        }

    @staticmethod
    def _fill_json(item) -> dict:
        if isinstance(item, dict):
            return {
                "key": item["key"],
                "name": item["name"],
                "options": list(item.get("options", [])),
            }
        if isinstance(item, tuple):
            key, name, options = item
            return {"key": key, "name": name, "options": list(options)}
        return {"key": item.key, "name": item.name, "options": list(getattr(item, "options", []))}

    @staticmethod
    def _signals_json(signals: list[Signal]) -> list[dict]:
        ordered = S.sort_signals([signal for signal in signals if isinstance(signal, Signal)])
        raw = [signal for signal in signals if isinstance(signal, dict)]
        return raw + [
            signal.to_json() if hasattr(signal, "to_json") else asdict(signal) for signal in ordered
        ]
