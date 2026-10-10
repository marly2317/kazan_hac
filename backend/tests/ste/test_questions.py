"""Выбор уточняющего вопроса и дозаполнение (архитектура §4.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.ste.domain.kb import CharSpec, Leaf, char_key
from apps.ste.domain.model import Confidence, ExtractedValue, Source, Status
from apps.ste.domain.narrowing import narrow
from apps.ste.domain.normalize import normalize_value, option_value
from apps.ste.domain.questions import (
    FILL_OPTIONS_LIMIT,
    UNKNOWN_VALUE,
    choose_question,
    family_question,
    fill_lists,
    leaf_question,
    remaining_phrase,
)
from apps.ste.domain.values import AllowedValue

KB_DIR = Path(__file__).resolve().parents[2] / "kb"
GRINDER = "28.24.11.000-00000042"
DESK = "31.01.10.000-00000006"
MATTRESS = "31.03.10.000-00000005"
COLD_CLOTHING = "14.12.30.160-00000340"
GEL_TOPPER = "31.03.12.140-00000069"
SANITARY = "14.12.10.000-00000005"  # два листа с одинаковой сигнатурой


@pytest.fixture(scope="module")
def kb():
    if not (KB_DIR / "ktru_kb.json.gz").exists():
        pytest.skip("база знаний КТРУ не собрана")
    from apps.ste.kb_build.build import load

    return load(KB_DIR)


def rng(low, high, okei="003") -> AllowedValue:
    return AllowedValue(kind="range", minimum=low, maximum=high, max_inclusive=False, okei=okei)


def txt(text: str) -> AllowedValue:
    return AllowedValue(kind="text", text=text)


def spec(name, allowed, type_=1, kind=2, required=False) -> CharSpec:
    return CharSpec(char_key(name), name, type_, kind, required, tuple(allowed))


def leaf(code, *specs) -> Leaf:
    return Leaf(code, "Лист " + code, "F", "00.00", {item.key: item for item in specs})


# --- выбор характеристики ---------------------------------------------------------------------


def test_picks_minimal_expected_remaining():
    # «Цвет» делит 4 листа пополам (E=2), «Вид» — на 4 по одному (E=1).
    leaves = [
        leaf(code, spec("Цвет", [txt(color)]), spec("Вид", [txt(kind)]))
        for code, color, kind in [
            ("1", "Синий", "А"),
            ("2", "Синий", "Б"),
            ("3", "Красный", "В"),
            ("4", "Красный", "Г"),
        ]
    ]
    question = choose_question(leaves, set(), set())
    assert question is not None
    assert question.char_key == "вид"
    assert question.kind == "char"
    assert question.title == "Уточните: Вид"
    assert question.why == "от этого зависит итоговый код (осталось 4 варианта)"
    assert [option.remaining for option in question.options] == [1, 1, 1, 1]
    assert [option.value for option in question.options] == ["А", "Б", "В", "Г"]
    assert question.allow_free_text


def test_known_and_skipped_are_not_asked():
    leaves = [
        leaf("1", spec("Цвет", [txt("Синий")]), spec("Вид", [txt("А")])),
        leaf("2", spec("Цвет", [txt("Красный")]), spec("Вид", [txt("Б")])),
    ]
    assert choose_question(leaves, {"вид"}, set()).char_key == "цвет"
    assert choose_question(leaves, set(), {"вид"}).char_key == "цвет"
    assert choose_question(leaves, {"вид"}, {"цвет"}) is None


def test_ties_prefer_kind1_then_required_then_fewer_options():
    def make(kind: int, required: bool) -> list[Leaf]:
        return [
            leaf(
                code,
                spec("Цвет", [txt(color)], kind=2),
                spec("Вид", [txt(name)], kind=kind, required=required),
            )
            for code, color, name in [("1", "А", "X"), ("2", "Б", "Y")]
        ]

    assert choose_question(make(1, False), set(), set()).char_key == "вид"
    assert choose_question(make(2, True), set(), set()).char_key == "вид"
    assert choose_question(make(2, False), set(), set()).char_key == "цвет"  # порядок листа
    fewer = [
        leaf("1", spec("Цвет", [txt("А"), txt("Б"), txt("В")]), spec("Вид", [txt("X")])),
        leaf("2", spec("Цвет", [txt("Г"), txt("Д"), txt("Е")]), spec("Вид", [txt("Y")])),
    ]
    assert choose_question(fewer, set(), set()).char_key == "вид"


def test_options_sorted_by_remaining_and_lacking_leaves_stay():
    leaves = [
        leaf("1", spec("Цвет", [txt("Синий")])),
        leaf("2", spec("Цвет", [txt("Синий")])),
        leaf("3", spec("Цвет", [txt("Красный")])),
        leaf("4"),  # характеристики нет — остаётся при любом ответе, как при сужении
    ]
    question = choose_question(leaves, set(), set(), include_unknown=True)
    assert [(option.value, option.remaining) for option in question.options] == [
        ("Синий", 3),
        ("Красный", 2),
        (UNKNOWN_VALUE, 4),
    ]
    assert question.options[-1].label == "Не знаю"


def test_no_question_when_answers_cannot_reduce():
    # Лист без характеристики выживает при любом ответе — вопрос ничего не сократит.
    leaves = [leaf("1", spec("Цвет", [txt("Синий")])), leaf("2")]
    assert choose_question(leaves, set(), set()) is None
    assert choose_question([leaf("1")], set(), set()) is None
    assert choose_question([], set(), set()) is None
    same = [leaf("1", spec("Цвет", [txt("Синий")])), leaf("2", spec("Цвет", [txt("синий")]))]
    assert choose_question(same, set(), set()) is None


def test_numeric_options_are_intervals_in_order():
    leaves = [
        leaf("1", spec("Длина", [rng(80, 100), rng(60, 80)], type_=2)),
        leaf("2", spec("Длина", [rng(60, 80)], type_=2)),
        # Пересекающийся интервал: ответ кнопкой сужает как число внутри интервала (как
        # после ответа при сужении): «≥60 <80» → 70 мм подходит и листу «≥65 <90».
        leaf("3", spec("Длина", [rng(65, 90)], type_=2)),
    ]
    question = choose_question(leaves, set(), set())
    assert [(option.value, option.remaining) for option in question.options] == [
        ("≥60 <80 Миллиметр", 3),
        ("≥65 <90 Миллиметр", 3),  # 77,5 мм — внутри всех трёх
        ("≥80 <100 Миллиметр", 1),  # 90 мм: «<90» у листа 3 строгая граница
    ]


def test_remaining_phrase_plural():
    assert remaining_phrase(1) == "остался 1 вариант"
    assert remaining_phrase(2) == "осталось 2 варианта"
    assert remaining_phrase(5) == "осталось 5 вариантов"
    assert remaining_phrase(11) == "осталось 11 вариантов"
    assert remaining_phrase(21) == "остался 21 вариант"
    assert remaining_phrase(112) == "осталось 112 вариантов"


# --- вопросы о семействе и листе ---------------------------------------------------------------


def test_family_question():
    question = family_question(
        [("F1", "Шуруповерт", 0.5), ("F2", "Дрель", 0.7)], leaf_counts={"F1": 3, "F2": 4}
    )
    assert question.kind == "family"
    assert question.char_key is None
    assert question.title == "Уточните вид товара"
    assert [(option.label, option.value, option.remaining) for option in question.options] == [
        ("Дрель", "F2", 4),
        ("Шуруповерт", "F1", 3),
    ]
    assert "2 типа" in question.why
    assert family_question([("F1", "А", 1.0)]).options[0].remaining == 0


def test_leaf_question_lists_codes():
    leaves = [leaf("1", spec("Цвет", [txt("Синий")])), leaf("2")]
    question = leaf_question(leaves)
    assert question.kind == "leaf"
    assert question.char_key is None
    assert not question.allow_free_text
    assert [option.value for option in question.options] == ["1", "2"]
    assert question.options[0].label == "1 — Лист 1 (Цвет: Синий)"
    assert question.options[1].label == "2 — Лист 2 (Цвет: —)"
    assert all(option.remaining == 1 for option in question.options)


# --- дозаполнение --------------------------------------------------------------------------------


def _ev(key, raw, unit=None):
    return ExtractedValue(key, Status.SPECIFIED, raw, unit, str(raw), True)


def test_fill_lists():
    fixed = spec("Вид", [txt("Куртка")], kind=1, required=True)
    single = spec("Подкладка", [txt("Есть")], kind=2)
    single_number = spec("Карманы", [AllowedValue(kind="concrete", concrete=2, okei="796")], 2)
    required = spec("Размер", [txt(str(size)) for size in range(40, 80)], required=True)
    optional = spec("Цвет", [txt("Синий"), txt("Красный")])
    given = spec("Ткань", [txt("Хлопок"), txt("Шерсть")], required=True)
    target = leaf("L", fixed, single, single_number, required, optional, given)
    values = {"ткань": normalize_value(given, _ev("ткань", "хлопок"))}

    result = fill_lists(target, values)
    assert set(result.by_ktru) == {"вид", "подкладка", "карманы"}
    assert result.by_ktru["вид"].texts == ["Куртка"]
    assert result.by_ktru["вид"].source == Source.KTRU
    assert result.by_ktru["карманы"].matched == ["= 2 Штука"]
    assert result.by_ktru["карманы"].number == 2
    assert [item.key for item in result.to_fill_required] == ["размер"]
    assert len(result.to_fill_required[0].options) == FILL_OPTIONS_LIMIT
    assert [item.key for item in result.to_fill_optional] == ["цвет"]
    assert result.to_fill_optional[0].to_json() == {
        "key": "цвет",
        "name": "Цвет",
        "options": ["Синий", "Красный"],
    }


def test_fill_lists_value_without_content_still_needs_filling():
    required = spec("Длина", [rng(60, 80), rng(80, 100)], type_=2, required=True)
    empty = normalize_value(required, _ev("длина", "много"))
    result = fill_lists(leaf("L", required), {"длина": empty})
    assert [item.key for item in result.to_fill_required] == ["длина"]
    assert result.to_fill_required[0].options == ["≥60 <80 Миллиметр", "≥80 <100 Миллиметр"]


# --- реальная база КТРУ -------------------------------------------------------------------------


@pytest.mark.parametrize("family_id", [GRINDER, DESK, MATTRESS, COLD_CLOTHING, GEL_TOPPER])
def test_option_counts_match_narrowing(kb, family_id):
    """Число «останется N» на кнопке — ровно то, что покажет сужение после ответа."""
    leaves = kb.leaves_of(family_id)
    question = choose_question(leaves, set(), set())
    assert question is not None
    family_char = kb.families[family_id].chars[question.char_key]
    for option in question.options:
        answer = option_value(family_char, option.value)
        assert answer is not None and answer.confidence == Confidence.CONFIRMED
        outcome = narrow(leaves, {question.char_key: answer})
        assert outcome.count == option.remaining, option.value
        assert option.remaining < len(leaves)


def test_cold_clothing_questions_reach_single_leaf(kb):
    family = kb.families[COLD_CLOTHING]
    leaves = kb.leaves_of(COLD_CLOTHING)
    assert len(leaves) == 112
    values: dict = {}
    asked = []
    while True:
        remaining = narrow(leaves, values).leaves
        question = choose_question(remaining, set(values), set())
        if question is None:
            break
        assert all(option.remaining < len(remaining) for option in question.options)
        asked.append(question.char_key)
        values[question.char_key] = option_value(
            family.chars[question.char_key], question.options[0].value
        )
    assert len(narrow(leaves, values).leaves) == 1
    assert asked[0] == "вид одежды"  # 14 вариантов по 8 листьев — самый информативный вопрос
    assert sorted(asked) == sorted(["вид одежды", "класс защиты одежды", "половая принадлежность"])


def test_grinder_asks_kind_first(kb):
    question = choose_question(kb.leaves_of(GRINDER), set(), set())
    assert question.char_key == "вид"
    assert len(question.options) == 8
    assert all(option.remaining == 1 for option in question.options)


def test_desk_asks_frame_type(kb):
    question = choose_question(kb.leaves_of(DESK), set(), set())
    assert question.char_key == "тип каркаса"  # kind=1 выигрывает равенство у «материала каркаса»


def test_identical_leaves_need_leaf_question(kb):
    leaves = kb.leaves_of(SANITARY)
    assert choose_question(leaves, set(), set()) is None
    question = leaf_question(leaves)
    assert [option.value for option in question.options] == [item.code for item in leaves]


def test_grinder_fill_lists(kb):
    family = kb.families[GRINDER]
    angle = kb.leaves["28.24.11.000-00000036"]
    values = {"вид": normalize_value(family.chars["вид"], _ev("вид", "Углошлифовальная машина"))}
    result = fill_lists(angle, values)
    required = [item.key for item in result.to_fill_required]
    assert "тип питания" in required
    assert "количество оборотов в минуту" in required
    assert "вид" not in required and "вид" not in result.by_ktru
    supply = next(item for item in result.to_fill_required if item.key == "тип питания")
    assert set(supply.options) == {"Пневматический", "Бензиновый", "От сети", "От аккумулятора"}
    # Без значения «Вид» неизменяемая характеристика заполняется из КТРУ.
    assert fill_lists(angle, {}).by_ktru["вид"].texts == ["Углошлифовальная машина"]
