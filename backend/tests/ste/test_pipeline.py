"""Сценарии пайплайна целиком: реальная база знаний, поиск и LLM — подставные.

Проверяется склейка шагов и сессия (архитектура §4.8): ответ кнопкой пересчитывает без LLM,
отказ LLM не роняет подбор, «не знаю» пропускает вопрос.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.ste.application import llm_steps
from apps.ste.application.llm_steps import ExtractionResult, SelectionResult
from apps.ste.application.pipeline import UNKNOWN_ANSWER, Deps, Pipeline, PipelineError
from apps.ste.domain.model import Confidence, ExtractedValue, FamilyHit, Source, Status
from apps.ste.infrastructure.session_store import MemorySessionStore
from apps.ste.kb_build.build import load

GRINDER = "28.24.11.000-00000042"
GRINDER_ANGLE_125 = "28.24.11.000-00000036"


@pytest.fixture(scope="module")
def kb():
    return load(Path(__file__).resolve().parents[2] / "kb")


class _Corrector:
    def correct(self, text):
        from types import SimpleNamespace

        return SimpleNamespace(original=text, corrected=text, fixes=[])


class _Retriever:
    """Поиск по словам наименования — достаточно, чтобы отличить семейства в тестах."""

    def __init__(self, kb, strong=False):
        self.kb = kb
        self.strong = strong

    def search(self, text, k=30):
        words = [word[:5] for word in text.lower().split() if len(word) > 3]
        scored = []
        for family in self.kb.selectable():
            score = sum(1 for word in words if word in family.name.lower())
            if score:
                scored.append((score, family))
        scored.sort(key=lambda item: -item[0])
        return [
            FamilyHit(family.id, family.short_id, family.name, float(score))
            for score, family in scored[:k]
        ]

    def confidence(self, hits):
        return {"strong": self.strong and bool(hits)}


class _Llm:
    """Маркер «LLM есть»: сами шаги подменяются monkeypatch."""


def _pipeline(kb, llm=None, strong=False):
    return Pipeline(
        Deps(
            kb=kb,
            corrector=_Corrector(),
            retriever=_Retriever(kb, strong=strong),
            llm=llm,
            store=MemorySessionStore(),
        )
    )


def _ev(key, value, unit=None, quote=None):
    return ExtractedValue(
        char_key=key,
        status=Status.SPECIFIED,
        value=value,
        unit=unit,
        quote=quote or str(value),
        quote_found=True,
        confidence=Confidence.CONFIDENT,
    )


@pytest.fixture
def fake_llm(monkeypatch, kb):
    calls = {"select": 0, "extract": 0}

    def select(client, text, cards, generic, allowed):
        calls["select"] += 1
        if "квадрокоптер" in text:
            return SelectionResult([], True, "не из категорий КТРУ")
        grinder = kb.families[GRINDER]
        return SelectionResult([(grinder.short_id, 0.95, "болгарка — шлифмашина")], False, None)

    def extract(client, text, family):
        calls["extract"] += 1
        values = {key: ExtractedValue(char_key=key, status=Status.ABSENT) for key in family.chars}
        if family.id == GRINDER and "болгарка" in text:
            values["вид"] = _ev("вид", "Углошлифовальная машина", quote="болгарка")
            if "125" in text:
                values["диаметр диска"] = _ev("диаметр диска", 125.0, "мм", "125мм")
            if "5 кг" in text:
                values["диаметр диска"] = _ev("диаметр диска", 5.0, "кг", "5 кг")
        return ExtractionResult(values=values, extra=[], brand="Makita", model=None)

    monkeypatch.setattr(llm_steps, "select_family", select)
    monkeypatch.setattr(llm_steps, "extract_values", extract)
    return calls


def test_llm_path_reaches_final_code(kb, fake_llm):
    pipeline = _pipeline(kb, llm=_Llm())
    # «Углошлифовальная машина» в КТРУ — ровно один лист шлифмашины.
    result = pipeline.analyze("болгарка makita 125мм")
    assert result["family"]["id"] == GRINDER
    assert result["final_code"] == GRINDER_ANGLE_125
    assert result["stage"] == "done"
    card = {item["key"]: item for item in result["card"]["characteristics"]}
    assert card["диаметр диска"]["ktru_value"].startswith("≥125 <130")
    assert result["card"]["brand"] == "Makita"
    assert fake_llm == {"select": 1, "extract": 1}


def test_question_then_button_answer_without_llm(kb, fake_llm):
    pipeline = _pipeline(kb, llm=_Llm())
    # Вид не назван — у шлифмашины 8 листьев, и первый вопрос — о виде.
    first = pipeline.analyze("шлифмашина makita")
    assert first["stage"] == "question"
    question = first["question"]
    assert question["kind"] == "char" and question["options"]
    option = question["options"][0]
    second = pipeline.answer(first["session_id"], question["char_key"], option["value"])
    assert second["leaves_count"] <= first["leaves_count"]
    assert second["leaves_count"] == option["remaining"] or second["stage"] == "done"
    assert fake_llm["extract"] == 1, "ответ кнопкой пересчитывается без LLM"


def test_unknown_skips_question(kb, fake_llm):
    pipeline = _pipeline(kb, llm=_Llm())
    first = pipeline.analyze("шлифмашина makita")
    key = first["question"]["char_key"]
    second = pipeline.answer(first["session_id"], key, UNKNOWN_ANSWER)
    assert second["question"] is None or second["question"]["char_key"] != key


def test_not_found(kb, fake_llm):
    result = _pipeline(kb, llm=_Llm()).analyze("квадрокоптер с камерой 4К")
    assert result["stage"] == "not_found"
    assert result["final_code"] is None
    assert any(signal["type"] == "NOT_FOUND" for signal in result["signals"])


def test_unit_mismatch_signal(kb, fake_llm):
    result = _pipeline(kb, llm=_Llm()).analyze("болгарка диск 5 кг")
    types = {signal["type"] for signal in result["signals"]}
    assert "UNIT_MISMATCH" in types


def test_llm_failure_degrades_to_rules(kb, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("сеть недоступна")

    monkeypatch.setattr(llm_steps, "select_family", broken)
    monkeypatch.setattr(llm_steps, "extract_values", broken)
    pipeline = _pipeline(kb, llm=_Llm(), strong=True)
    result = pipeline.analyze("шлифмашина углошлифовальная машина")
    assert result["family"]["id"] == GRINDER
    assert result["stage"] in {"done", "question"}


def test_conflict_out_of_range(kb):
    pipeline = _pipeline(kb, strong=True)
    result = pipeline.analyze("матрац длина 500 см")
    assert result["stage"] == "conflict"
    types = {signal["type"] for signal in result["signals"]}
    assert {"OUT_OF_RANGE", "CONFLICT"} <= types


def test_edit_and_choose_family(kb):
    pipeline = _pipeline(kb, strong=False)
    first = pipeline.analyze("стол письменный")
    sid = first["session_id"]
    chosen = pipeline.choose_family(sid, "31.01.10.000-00000006")
    assert chosen["family"]["id"] == "31.01.10.000-00000006"
    key = chosen["question"]["char_key"] if chosen["question"] else None
    if key:
        option = chosen["question"]["options"][0]["value"]
        edited = pipeline.edit(sid, key, option)
        assert edited["leaves_count"] <= chosen["leaves_count"]


def test_answer_without_session_is_an_error(kb):
    with pytest.raises(PipelineError):
        _pipeline(kb).answer("нет-такой-сессии", "вид", "x")


def test_answer_text_unknown_is_skip(kb, fake_llm, monkeypatch):
    monkeypatch.setattr(llm_steps, "extract_one", lambda *args, **kwargs: None)
    pipeline = _pipeline(kb, llm=_Llm())
    first = pipeline.analyze("шлифмашина makita")
    key = first["question"]["char_key"]
    second = pipeline.answer_text(first["session_id"], key, "не знаю")
    assert not any(
        signal["type"] == "VALUE_NOT_ALLOWED" and signal["char_key"] == key
        for signal in second["signals"]
    )
    assert Source.ANSWER.value == "answer"
