"""Сужение листьев, воронка, конфликт и различия листьев (архитектура §4.5–4.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.ste.domain.kb import CharSpec, Leaf, char_key
from apps.ste.domain.model import Confidence, ExtractedValue, NormalizedValue, Status
from apps.ste.domain.narrowing import (
    PENALTY_MISSING,
    PENALTY_WEAK,
    RankedLeaf,
    allowed_descriptions,
    distinguishing_keys,
    leaf_distinctions,
    narrow,
    nearest_allowed,
    summarize_spec,
)
from apps.ste.domain.normalize import normalize_value, option_value
from apps.ste.domain.values import AllowedValue

KB_DIR = Path(__file__).resolve().parents[2] / "kb"
GRINDER = "28.24.11.000-00000042"
MATTRESS = "31.03.10.000-00000005"
COLD_CLOTHING = "14.12.30.160-00000340"


@pytest.fixture(scope="module")
def kb():
    if not (KB_DIR / "ktru_kb.json.gz").exists():
        pytest.skip("база знаний КТРУ не собрана")
    from apps.ste.kb_build.build import load

    return load(KB_DIR)


# --- синтетика --------------------------------------------------------------------------------


def rng(low, high, okei="003", low_inc=True, high_inc=False) -> AllowedValue:
    return AllowedValue(
        kind="range",
        minimum=low,
        min_inclusive=low_inc,
        maximum=high,
        max_inclusive=high_inc,
        okei=okei,
    )


def txt(text: str) -> AllowedValue:
    return AllowedValue(kind="text", text=text)


def spec(name, allowed, type_=1, kind=2, required=False) -> CharSpec:
    return CharSpec(char_key(name), name, type_, kind, required, tuple(allowed))


def leaf(code, *specs, family="F") -> Leaf:
    return Leaf(code, "Лист " + code, family, "00.00", {item.key: item for item in specs})


def value(char: CharSpec, raw, unit=None, confidence=Confidence.CONFIDENT) -> NormalizedValue:
    """Значение пользователя, нормализованное по характеристике ``char``."""
    extracted = ExtractedValue(
        char_key=char.key,
        status=Status.SPECIFIED,
        value=raw,
        unit=unit,
        quote=str(raw),
        quote_found=confidence != Confidence.WEAK,
        confidence=confidence if confidence == Confidence.CONFIRMED else Confidence.WEAK,
    )
    return normalize_value(char, extracted)


KIND = spec("Вид", [txt("Куртка"), txt("Брюки")], kind=1, required=True)
SEX = spec("Пол", [txt("Мужской"), txt("Женский")], kind=1, required=True)
SIZE = spec("Длина", [rng(60, 80), rng(80, 100)], type_=2)


def clothes() -> list[Leaf]:
    """Декартово произведение «Вид × Пол» + у брюк своя «Длина»."""
    return [
        leaf("A1", spec("Вид", [txt("Куртка")], kind=1), spec("Пол", [txt("Мужской")], kind=1)),
        leaf("A2", spec("Вид", [txt("Куртка")], kind=1), spec("Пол", [txt("Женский")], kind=1)),
        leaf(
            "B1",
            spec("Вид", [txt("Брюки")], kind=1),
            spec("Пол", [txt("Мужской")], kind=1),
            spec("Длина", [rng(60, 80)], type_=2),
        ),
        leaf(
            "B2",
            spec("Вид", [txt("Брюки")], kind=1),
            spec("Пол", [txt("Женский")], kind=1),
            spec("Длина", [rng(80, 100)], type_=2),
        ),
    ]


# --- сужение ------------------------------------------------------------------------------------


def test_confident_value_excludes():
    outcome = narrow(clothes(), {"вид": value(KIND, "куртка")})
    assert outcome.codes == ["A1", "A2"]
    assert outcome.excluded == {"B1": ["вид"], "B2": ["вид"]}
    assert outcome.total == 4
    assert outcome.count == 2
    assert outcome.conflict is None
    assert all(isinstance(item, RankedLeaf) for item in outcome.remaining)
    assert [item.code for item in outcome.leaves] == ["A1", "A2"]


def test_weak_value_only_penalizes():
    weak = value(KIND, "куртка", confidence=Confidence.WEAK)
    assert weak.confidence == Confidence.WEAK
    outcome = narrow(clothes(), {"вид": weak})
    assert outcome.count == 4
    assert outcome.excluded == {}
    scores = {item.code: item.score for item in outcome.remaining}
    assert scores["A1"] == scores["A2"] == 1.0
    assert scores["B1"] == pytest.approx(PENALTY_WEAK)
    assert outcome.codes[:2] == ["A1", "A2"]
    b1 = next(item for item in outcome.remaining if item.code == "B1")
    assert b1.weak_mismatches == ("вид",)


def test_missing_characteristic_is_neutral_with_penalty():
    outcome = narrow(clothes(), {"длина": value(SIZE, 70, "см")})
    # 70 см = 700 мм: вне обоих интервалов брюк — брюки исключены, у курток длины нет.
    assert outcome.codes == ["A1", "A2"]
    assert outcome.remaining[0].score == pytest.approx(PENALTY_MISSING)
    assert outcome.remaining[0].missing == ("длина",)
    fits = narrow(clothes(), {"длина": value(SIZE, 70, "мм")})
    assert fits.codes == ["B1", "A1", "A2"]  # B1 без штрафа — выше листьев без характеристики


def test_family_score_multiplies():
    leaves = clothes() + [leaf("Z1", spec("Вид", [txt("Куртка")], kind=1), family="G")]
    outcome = narrow(leaves, {"вид": value(KIND, "Куртка")}, {"F": 0.9, "G": 0.5})
    scores = {item.code: item.score for item in outcome.remaining}
    assert scores == {"A1": 0.9, "A2": 0.9, "Z1": 0.5}
    assert outcome.codes == ["A1", "A2", "Z1"]


def test_values_without_content_are_ignored():
    empty = value(SIZE, "много")
    outcome = narrow(clothes(), {"длина": empty})
    assert outcome.count == 4
    assert all(item.score == 1.0 for item in outcome.remaining)
    assert outcome.steps == []


def test_all_strong_mismatches_are_listed():
    values = {"вид": value(KIND, "Брюки"), "пол": value(SEX, "Женский")}
    outcome = narrow(clothes(), values)
    assert outcome.codes == ["B2"]
    assert outcome.excluded["A1"] == ["вид", "пол"]
    assert outcome.excluded["A2"] == ["вид"]


def test_steps_apply_kind1_first():
    values = {
        "длина": value(SIZE, 90, "мм"),  # не kind=1 — применяется после
        "пол": value(SEX, "женский"),
        "вид": value(KIND, "брюки"),
    }
    outcome = narrow(clothes(), values)
    assert [(step.level, step.label, step.count) for step in outcome.steps] == [
        ("Пол", "Женский", 2),
        ("Вид", "Брюки", 1),
        ("Длина", "90 Миллиметр", 1),
    ]
    assert outcome.codes == ["B2"]


def test_step_label_for_interval_answer():
    answer = option_value(SIZE, "≥80 <100 Миллиметр")
    outcome = narrow(clothes(), {"длина": answer})
    assert outcome.codes == ["B2", "A1", "A2"]
    assert [(step.label, step.count) for step in outcome.steps] == [("≥80 <100 Миллиметр", 3)]


def test_empty_leaves():
    outcome = narrow([], {"вид": value(KIND, "Куртка")})
    assert outcome.remaining == [] and outcome.conflict is None and outcome.total == 0


# --- конфликт -----------------------------------------------------------------------------------


def test_conflict_single_culprit_and_nearest():
    leaves = [
        leaf(
            "M1", spec("Тип", [txt("Пружинный")], kind=1), spec("Длина", [rng(190, 200, "004")], 2)
        ),
        leaf(
            "M2", spec("Тип", [txt("Беспружинный")], kind=1), spec("Длина", [rng(60, 80, "004")], 2)
        ),
    ]
    kind = spec("Тип", [txt("Пружинный"), txt("Беспружинный")], kind=1)
    length = spec("Длина", [rng(190, 200, "004"), rng(60, 80, "004")], type_=2)
    values = {"тип": value(kind, "Пружинный"), "длина": value(length, 5, "м")}
    outcome = narrow(leaves, values)
    assert outcome.remaining == []
    conflict = outcome.conflict
    assert conflict is not None
    assert conflict.excludes_alone == {"тип": 1, "длина": 2}
    assert conflict.culprit_sets == [("длина",)]
    [culprit] = conflict.culprits
    assert culprit.char_key == "длина"
    assert culprit.excludes_alone == 2
    assert culprit.restores == 1
    assert culprit.allowed == ["≥190 <200 Сантиметр"]
    assert culprit.nearest == "≥190 <200 Сантиметр"
    assert culprit.user_value == "5 Метр"
    assert culprit.together_with == []


def test_conflict_needs_two_values_changed():
    leaves = [
        leaf("X", spec("Цвет", [txt("Красный")]), spec("Ткань", [txt("Хлопок")])),
        leaf("Y", spec("Цвет", [txt("Синий")]), spec("Ткань", [txt("Шерсть")])),
    ]
    color = spec("Цвет", [txt("Красный"), txt("Синий"), txt("Зелёный")])
    cloth = spec("Ткань", [txt("Хлопок"), txt("Шерсть"), txt("Лён")])
    values = {"цвет": value(color, "Зелёный"), "ткань": value(cloth, "Лён")}
    conflict = narrow(leaves, values).conflict
    assert conflict.culprit_sets == [("цвет", "ткань")]
    assert [item.char_key for item in conflict.culprits] == ["цвет", "ткань"]
    assert conflict.culprits[0].together_with == ["ткань"]
    assert conflict.culprits[0].restores == 2


def test_conflict_prefers_text_values_over_confirmed_and_kind1():
    leaves = [
        leaf("P", spec("Вид", [txt("Полировальная")], kind=1), spec("Питание", [txt("От сети")])),
        leaf("S", spec("Вид", [txt("Шлифовальная")], kind=1), spec("Питание", [txt("Пневмо")])),
    ]
    kind = spec("Вид", [txt("Полировальная"), txt("Шлифовальная")], kind=1)
    supply = spec("Питание", [txt("От сети"), txt("Пневмо")])
    values = {"вид": value(kind, "Полировальная"), "питание": value(supply, "Пневмо")}
    conflict = narrow(leaves, values).conflict
    assert sorted(conflict.culprit_sets) == [("вид",), ("питание",)]
    # «Что за товар» (kind=1) пользователь путает реже — первым подозреваем «Питание».
    assert [item.char_key for item in conflict.culprits] == ["питание", "вид"]
    assert conflict.culprits[0].nearest == "От сети"
    # Подтверждённое пользователем подозреваем последним.
    values["питание"] = value(supply, "Пневмо", confidence=Confidence.CONFIRMED)
    conflict = narrow(leaves, values).conflict
    assert [item.char_key for item in conflict.culprits] == ["вид", "питание"]


def test_conflict_with_unit_mismatch_confirmed():
    char = spec("Длина", [rng(60, 80)], type_=2)
    edited = value(char, 5, "кг", confidence=Confidence.CONFIRMED)
    outcome = narrow([leaf("L", char)], {"длина": edited})
    assert outcome.remaining == []
    culprit = outcome.conflict.culprits[0]
    assert culprit.char_key == "длина"
    assert culprit.nearest is None  # килограммы на шкале длины не к чему приблизить
    assert culprit.allowed == ["≥60 <80 Миллиметр"]


def test_nearest_allowed_numeric_and_text():
    specs = [spec("Длина", [rng(10, 20, "004"), rng(300, 400)], type_=2)]
    near_cm = value(specs[0], 24, "см")  # 40 мм до «<20 см» против 60 мм до «≥300 мм»
    assert nearest_allowed(specs, near_cm) == "≥10 <20 Сантиметр"
    near_mm = value(specs[0], 290, "мм")
    assert nearest_allowed(specs, near_mm) == "≥300 <400 Миллиметр"
    texts = [spec("Материал", [txt("Хлопок"), txt("Шерсть")])]
    unmapped = value(texts[0], "Хлопчатобумажная")
    assert unmapped.texts == []
    assert nearest_allowed(texts, unmapped) == "Хлопок"
    assert allowed_descriptions(specs) == ["≥10 <20 Сантиметр", "≥300 <400 Миллиметр"]


def test_no_conflict_without_strong_values():
    outcome = narrow(clothes(), {"вид": value(KIND, "Шуба", confidence=Confidence.WEAK)})
    assert outcome.count == 4
    assert outcome.conflict is None


# --- различия листьев -----------------------------------------------------------------------


def test_leaf_distinctions_skip_common_and_order_kind1_first():
    leaves = [
        leaf("A", spec("Цвет", [txt("Синий")]), spec("Вид", [txt("Куртка")], kind=1)),
        leaf(
            "B",
            spec("Цвет", [txt("Синий")]),
            spec("Вид", [txt("Брюки")], kind=1),
            spec("Длина", [rng(60, 80)], type_=2),
        ),
    ]
    assert distinguishing_keys(leaves) == ["вид", "длина"]
    rows = leaf_distinctions(leaves)
    assert rows == {
        "A": [("Вид", "Куртка"), ("Длина", "—")],
        "B": [("Вид", "Брюки"), ("Длина", "≥60 <80 Миллиметр")],
    }
    assert leaf_distinctions(leaves, limit_chars=1)["B"] == [("Вид", "Брюки")]
    assert leaf_distinctions(leaves[:1]) == {"A": []}


def test_summarize_long_numeric_list():
    many = spec("Мощность", [rng(100 * i, 100 * (i + 1), "212") for i in range(5, 10)], type_=2)
    assert summarize_spec(many) == "≥500 <600 Ватт … ≥900 <1000 Ватт (5 знач.)"
    texts = spec("Цвет", [txt(name) for name in ("А", "Б", "В", "Г", "Д")])
    assert summarize_spec(texts) == "А, Б, В и ещё 2"
    assert summarize_spec(None) == "—"


# --- реальная база КТРУ ---------------------------------------------------------------------


def _values(kb, family_id, *items):
    family = kb.families[family_id]
    result = {}
    for key, raw, unit in items:
        extracted = ExtractedValue(
            char_key=key,
            status=Status.SPECIFIED,
            value=raw,
            unit=unit,
            quote=str(raw),
            quote_found=True,
        )
        result[key] = normalize_value(family.chars[key], extracted)
    return result


def test_grinder_angle_125mm_gives_single_leaf(kb):
    values = _values(
        kb,
        GRINDER,
        ("вид", "Углошлифовальная машина", None),
        ("диаметр диска", 125, "мм"),
        ("мощность", "0,84", "кВт"),
    )
    outcome = narrow(kb.leaves_of(GRINDER), values, {GRINDER: 0.9})
    assert outcome.codes == ["28.24.11.000-00000036"]
    assert outcome.remaining[0].score == pytest.approx(0.9)
    assert [step.count for step in outcome.steps] == [1, 1, 1]
    assert outcome.steps[0].level == "Вид"
    assert len(outcome.excluded) == 7


def test_grinder_disc_only_keeps_leaves_without_disc(kb):
    outcome = narrow(kb.leaves_of(GRINDER), _values(kb, GRINDER, ("диаметр диска", 125, "мм")))
    assert outcome.count == 8
    penalized = [item.code for item in outcome.remaining if item.missing]
    assert outcome.codes[-len(penalized) :] == penalized  # без характеристики — в конце списка
    assert all(
        item.score == pytest.approx(PENALTY_MISSING) for item in outcome.remaining if item.missing
    )


def test_mattress_500cm_conflict(kb):
    values = _values(kb, MATTRESS, ("тип матраца", "Пружинный", None), ("длина", 5, "м"))
    outcome = narrow(kb.leaves_of(MATTRESS), values)
    assert outcome.remaining == []
    assert [step.count for step in outcome.steps] == [1, 0]
    conflict = outcome.conflict
    assert conflict.culprit_sets == [("длина",)]
    culprit = conflict.culprits[0]
    assert culprit.char_key == "длина"
    assert culprit.excludes_alone == 3
    assert culprit.nearest == ">245 ≤250 Сантиметр"
    assert culprit.restores == 1


def test_cold_clothing_three_kind1_values(kb):
    values = _values(
        kb,
        COLD_CLOTHING,
        ("класс защиты одежды", "3 (IV климатический пояс)", None),
        ("вид одежды", "куртка", None),
        ("половая принадлежность", "мужская", None),
    )
    outcome = narrow(kb.leaves_of(COLD_CLOTHING), values)
    assert outcome.count == 1
    counts = [step.count for step in outcome.steps]
    assert counts == sorted(counts, reverse=True) and counts[-1] == 1
    leaf_chars = outcome.leaves[0].chars
    assert leaf_chars["вид одежды"].allowed[0].text == "Куртка"
    assert leaf_chars["половая принадлежность"].allowed[0].text == "Мужской"


def test_grinder_distinctions_start_with_kind(kb):
    rows = leaf_distinctions(kb.leaves_of(GRINDER))
    assert len(rows) == 8
    assert all(row[0][0] == "Вид" for row in rows.values())
    assert rows["28.24.11.000-00000036"][0] == ("Вид", "Углошлифовальная машина")
    assert all(len(row) == 3 for row in rows.values())


# --- градации и интервалы неизменяемых характеристик -------------------------------------------


def _grade(code, low):
    return leaf(code, spec("Свинцовый эквивалент", [rng(low, None)], type_=2, kind=1))


def test_number_goes_to_the_narrowest_grade():
    """0,5 попадает в «≥0,25», «≥0,35» и «≥0,5»; КТРУ относит товар к самой узкой градации."""
    leaves = [_grade("G25", 0.25), _grade("G35", 0.35), _grade("G50", 0.5)]
    char = leaves[0].chars["свинцовый эквивалент"]
    assert narrow(leaves, {char.key: value(char, 0.5, "мм", Confidence.CONFIRMED)}).codes == ["G50"]
    assert narrow(leaves, {char.key: value(char, 0.3, "мм", Confidence.CONFIRMED)}).codes == ["G25"]


def test_grade_button_reaches_its_own_leaf():
    leaves = [_grade("G25", 0.25), _grade("G50", 0.5)]
    char = leaves[0].chars["свинцовый эквивалент"]
    for code, label in (("G25", "≥0,25 Миллиметр"), ("G50", "≥0,5 Миллиметр")):
        answer = option_value(char, label) or option_value(leaves[1].chars[char.key], label)
        assert narrow(leaves, {char.key: answer}).codes == [code]


def test_overlapping_closed_intervals_button_is_exact_for_kind1():
    """«≥60 ≤82» и «≥71 ≤80»: служебная точка 71 попадает в оба, но кнопка — интервал листа."""
    wide = leaf("W", spec("Длина", [rng(60, 82, high_inc=True)], type_=2, kind=1))
    tight = leaf("T", spec("Длина", [rng(71, 80, high_inc=True)], type_=2, kind=1))
    answer = option_value(wide.chars["длина"], "≥60 ≤82 Миллиметр")
    assert narrow([wide, tight], {"длина": answer}).codes == ["W"]
    # Число из текста в пересечении закрытых интервалов — неоднозначность, её снимает вопрос.
    typed = value(wide.chars["длина"], 75, "мм", Confidence.CONFIRMED)
    assert sorted(narrow([wide, tight], {"длина": typed}).codes) == ["T", "W"]
