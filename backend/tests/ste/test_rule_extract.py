"""Запасной разбор без LLM: значения семейства дословно и числа с единицами."""

from pathlib import Path

import pytest

from apps.ste.application.rule_extract import extract_by_rules
from apps.ste.domain.model import Status
from apps.ste.kb_build.build import load


@pytest.fixture(scope="module")
def kb():
    return load(Path(__file__).resolve().parents[2] / "kb")


def _family(kb, name):
    return next(f for f in kb.selectable() if f.name == name)


def test_grinder_kind_and_numbers(kb):
    family = _family(kb, "Шлифмашина")
    found = extract_by_rules("Углошлифовальная машина Makita 125 мм, мощность 840 Вт", family)
    assert found["вид"].value == "Углошлифовальная машина"
    assert found["вид"].status is Status.SPECIFIED
    assert found["мощность"].value == 840.0
    assert found["мощность"].unit.lower() == "вт"


def test_number_without_hint_and_ambiguous_dimension_is_left_alone(kb):
    family = _family(kb, "Шлифмашина")
    found = extract_by_rules("шлифмашина 125", family)
    # Без единицы и без слова-подсказки число не приписывается никому.
    assert all(ev.value != 125.0 for ev in found.values())


def test_inflected_text_value(kb):
    family = _family(kb, "Стол письменный")
    found = extract_by_rules("стол письменный на металлическом каркасе", family)
    values = {ev.value for ev in found.values()}
    assert any("еталл" in str(value) for value in values)
