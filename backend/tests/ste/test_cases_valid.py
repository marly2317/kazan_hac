"""Проверка тест-кейсов ``cases.jsonl`` по базе знаний КТРУ (архитектура §3.11, §9).

Метки кейсов ручные («серебряные»), поэтому проверяем не качество пайплайна, а то, что сами
метки не врут: семейства и листья существуют и согласованы между собой, ожидаемые значения —
это реальные допустимые значения КТРУ (ровно в записи ``describe()``), а ожидаемый лист
действительно однозначно следует из этих значений. Битая метка дала бы ложную ошибку в замере
и спрятала бы настоящую.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from apps.ste.domain import units as U
from apps.ste.domain.kb import char_key
from apps.ste.domain.model import SignalType, Stage

CASES_PATH = Path(__file__).resolve().parent / "cases.jsonl"
KB_DIR = Path(__file__).resolve().parents[2] / "kb"

FIELDS = {
    "id",
    "category",
    "variant",
    "source_ste_id",
    "text",
    "expected_family",
    "expected_family_name",
    "acceptable_families",
    "expected_leaf",
    "expected_stage",
    "expected_signals",
    "expected_values",
    "notes",
}
VARIANTS = {"full", "short", "missing", "colloquial", "typo", "error", "absent", "demo"}
CATEGORIES = {"одежда", "мебель", "инструменты", "обувь", "сумки и рюкзаки", "вне охвата"}
#: Стадии, которые кейс может ожидать сразу после анализа текста (без ответов пользователя).
STAGES = {Stage.DONE.value, Stage.QUESTION.value, Stage.NOT_FOUND.value, Stage.CONFLICT.value}
#: Сигналы, ради которых существуют кейсы «error»: несоответствие справочнику или самому себе.
ERROR_SIGNALS = {
    SignalType.OUT_OF_RANGE.value,
    SignalType.UNIT_MISMATCH.value,
    SignalType.VALUE_NOT_ALLOWED.value,
    SignalType.CONTRADICTION.value,
    SignalType.DUPLICATE_VALUE.value,
    SignalType.CONFLICT.value,
}


def _load_cases() -> list[dict]:
    lines = CASES_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


CASES = _load_cases()


@pytest.fixture(scope="module")
def kb():
    if not (KB_DIR / "ktru_kb.json.gz").exists():
        pytest.skip("база знаний КТРУ не собрана")
    from apps.ste.kb_build.build import load

    return load(KB_DIR)


def _values(value) -> list[str]:
    """kind=3 записан списком, остальные — строкой."""
    return list(value) if isinstance(value, list) else [value]


def _compatible_leaves(kb, case: dict) -> list[str]:
    """Листья семейства, совместимые с ``expected_values`` по описаниям допустимых значений.

    Как в сужении (архитектура §4.5): лист исключается, если характеристика у него есть, а
    значения среди допустимых нет; отсутствие характеристики у листа нейтрально.
    """
    out = []
    for leaf in kb.leaves_of(case["expected_family"]):
        compatible = True
        for name, value in case["expected_values"].items():
            spec = leaf.chars.get(char_key(name))
            if spec is None:
                continue
            allowed = {item.describe() for item in spec.allowed}
            if not all(item in allowed for item in _values(value)):
                compatible = False
                break
        if compatible:
            out.append(leaf.code)
    return out


# --- файл целиком ------------------------------------------------------------------------------


def test_every_case_has_exactly_the_contract_fields():
    for case in CASES:
        assert set(case) == FIELDS, case.get("id")


def test_ids_and_texts_are_unique():
    ids = [case["id"] for case in CASES]
    texts = [case["text"].strip().casefold() for case in CASES]
    assert len(ids) == len(set(ids)), [i for i, n in Counter(ids).items() if n > 1]
    assert len(texts) == len(set(texts))
    assert all(text for text in texts)


def test_distribution_covers_every_scenario():
    variants = Counter(case["variant"] for case in CASES)
    assert set(variants) <= VARIANTS
    assert variants["demo"] == 10
    assert variants["error"] >= 10
    assert variants["typo"] >= 8
    assert variants["absent"] >= 8
    for name in ("full", "short", "missing", "colloquial"):
        assert variants[name] >= 5, name
    stages = Counter(case["expected_stage"] for case in CASES)
    # Каждый исход пайплайна должен встречаться — иначе его поломку замер не увидит.
    for stage in STAGES:
        assert stages[stage] >= 3, stage
    categories = Counter(case["category"] for case in CASES)
    for name in ("одежда", "мебель", "инструменты", "обувь", "сумки и рюкзаки"):
        assert categories[name] >= 3, name


def test_demo_scenarios_are_the_ten_from_architecture():
    demo = sorted(case["id"] for case in CASES if case["variant"] == "demo")
    assert demo == [f"demo-{index:02d}" for index in range(1, 11)]


# --- каждый кейс -------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_case_labels_agree_with_kb(kb, case):
    assert case["variant"] in VARIANTS
    assert case["category"] in CATEGORIES
    assert case["expected_stage"] in STAGES | {None}
    assert isinstance(case["notes"], str)
    ste_id = case["source_ste_id"]
    assert ste_id is None or (isinstance(ste_id, str) and ste_id.isdigit())

    signals = case["expected_signals"]
    known = {item.value for item in SignalType}
    assert isinstance(signals, list) and len(signals) == len(set(signals))
    assert set(signals) <= known, set(signals) - known

    acceptable = case["acceptable_families"]
    assert isinstance(acceptable, list) and len(acceptable) == len(set(acceptable))
    for family_id in acceptable:
        assert family_id in kb.families, family_id
        assert kb.families[family_id].role in {"family", "generic"}, family_id
        assert family_id != case["expected_family"]

    family_id = case["expected_family"]
    if family_id is None:
        # Товара нет в КТРУ: честное «не нашли»; запасной вариант — только общая позиция.
        assert case["expected_family_name"] is None
        assert case["expected_leaf"] is None
        assert case["expected_values"] == {}
        assert case["expected_stage"] == Stage.NOT_FOUND.value
        assert SignalType.NOT_FOUND.value in signals
        for item in acceptable:
            assert kb.families[item].role == "generic", item
        return

    assert family_id in kb.families, family_id
    family = kb.families[family_id]
    assert family.role in {"family", "generic"}
    assert case["expected_family_name"] == family.name
    assert case["category"] == family.category
    assert case["expected_stage"] != Stage.NOT_FOUND.value

    leaf_code = case["expected_leaf"]
    if leaf_code is not None:
        assert leaf_code in kb.leaves, leaf_code
        assert kb.leaves[leaf_code].family_id == family_id
        assert leaf_code in family.leaves
    # Лист определён ⇔ стадия «done».
    assert (leaf_code is not None) == (case["expected_stage"] == Stage.DONE.value)

    for name, value in case["expected_values"].items():
        char = family.chars.get(char_key(name))
        assert char is not None, f"нет характеристики «{name}» в семействе"
        assert char.name == name, (char.name, name)
        allowed = {item.describe() for item in char.allowed}
        for item in _values(value):
            assert item in allowed, f"«{name}»: «{item}» нет среди допустимых значений КТРУ"

    compatible = _compatible_leaves(kb, case)
    if leaf_code is not None:
        assert leaf_code in compatible, "ожидаемый лист противоречит ожидаемым значениям"
        if len(family.leaves) > 1:
            # Значения должны однозначно указывать на лист — иначе метка «done» не обоснована.
            assert compatible == [leaf_code], compatible
    if case["expected_stage"] == Stage.QUESTION.value:
        assert len(family.leaves) > 1
        assert len(compatible) >= 2, "по ожидаемым значениям лист уже определён — это не вопрос"
    if case["expected_stage"] == Stage.CONFLICT.value:
        assert set(signals) & ERROR_SIGNALS, "конфликт без сигнала о причине"


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case["variant"] in {"error", "typo", "absent"}],
    ids=lambda case: case["id"],
)
def test_special_variants_expect_their_signal(case):
    signals = set(case["expected_signals"])
    if case["variant"] == "error":
        assert signals & ERROR_SIGNALS
    elif case["variant"] == "typo":
        assert SignalType.TYPO.value in signals
    else:
        assert case["expected_family"] is None
        assert SignalType.NOT_FOUND.value in signals


_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)\s*([a-zа-яё°\"/·*.]+)?", re.IGNORECASE)
_WORD_NUMBERS = {"одно": 1.0, "двух": 2.0, "трех": 3.0, "четырех": 4.0}


def _numbers_and_units(text: str):
    """Числа текста и все распознаваемые единицы рядом с ними («125мм», «2Ач», «5 метров»)."""
    numbers = [float(match.group(1).replace(",", ".")) for match in _NUMBER.finditer(text)]
    # Количество словом («двухместный диван») — поставщики так пишут, кейсы это сохраняют.
    for stem in re.findall(r"(одно|двух|тр[её]х|четыр[её]х)мест", text.casefold()):
        numbers.append(_WORD_NUMBERS[stem.replace("ё", "е")])
    tails = [match.group(2) for match in _NUMBER.finditer(text) if match.group(2)]
    tokens = re.findall(r"[^\s\d,;()]+", text)
    found = {U.parse_unit(word.strip(".")) for word in tails + tokens}
    return numbers, [unit for unit in found if unit is not None]


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case["expected_values"]],
    ids=lambda case: case["id"],
)
def test_numeric_expected_values_contain_a_number_from_text(kb, case):
    """Интервал в метке должен содержать число, написанное в тексте (с переводом единиц).

    Ловит самую опасную ошибку разметки: соседний интервал вместо нужного — он тоже есть в КТРУ,
    и проверка «значение существует» его пропустит.
    """
    numbers, units = _numbers_and_units(case["text"])
    family = kb.families[case["expected_family"]]
    for name, value in case["expected_values"].items():
        char = family.chars[char_key(name)]
        for description in _values(value):
            allowed = [item for item in char.allowed if item.describe() == description]
            numeric = [item for item in allowed if item.is_numeric]
            if not numeric:
                continue
            hit = False
            for item in numeric:
                for number in numbers:
                    for unit in [*units, None]:
                        target = item.unit
                        converted = number
                        if unit is not None and target is not None:
                            if not U.same_dimension(unit, target):
                                continue
                            converted = U.convert(number, unit, target)
                        if converted is not None and item.contains_number(converted):
                            hit = True
            assert hit, f"«{name}» = «{description}»: в тексте нет подходящего числа"


def test_numeric_expected_values_use_strict_bounds_from_kb(kb):
    """Граница интервала — ровно как в КТРУ: 125 мм у УШМ — «≥125 <130», а не «≥115 <125»."""
    case = next(case for case in CASES if case["id"] == "demo-01")
    assert case["expected_values"]["Диаметр диска"] == "≥125 <130 Миллиметр"
    leaf = kb.leaves[case["expected_leaf"]]
    spec = leaf.chars[char_key("Диаметр диска")]
    hits = [value.describe() for value in spec.allowed if value.contains_number(125)]
    assert hits == ["≥125 <130 Миллиметр"]
    # Матрац: левая граница строгая, 180 см — в «>175 ≤180», а не в «>180 ≤185».
    mattress = next(case for case in CASES if case["id"] == "furn-17")
    assert mattress["expected_values"]["Длина"] == ">175 ≤180 Сантиметр"


def test_out_of_range_cases_really_miss_every_interval(kb):
    """Кейсы OUT_OF_RANGE: значение вне всех допустимых у семейства (иначе это не ошибка)."""
    probes = {
        "demo-07": ("Длина", 5, "м"),
        "err-01": ("Высота", 45, "см"),
        "err-02": ("Диаметр посадочного отверстия диска", 22.2, "мм"),
        "err-03": ("Рост", 210, "см"),
        "err-04": ("Потребляемая мощность", 320, "Вт"),
    }
    by_id = {case["id"]: case for case in CASES}
    for case_id, (name, number, unit_text) in probes.items():
        case = by_id[case_id]
        assert "OUT_OF_RANGE" in case["expected_signals"]
        char = kb.families[case["expected_family"]].chars[char_key(name)]
        unit = U.parse_unit(unit_text)
        for value in char.allowed:
            converted = U.convert(number, unit, value.unit)
            assert converted is not None, (case_id, value.describe())
            assert not value.contains_number(converted), (case_id, value.describe())


def test_unit_mismatch_cases_really_cross_dimensions(kb):
    probes = {
        "demo-08": ("Толщина материала столешницы", "кг"),
        "err-05": ("Диаметр диска", "Вт"),
        "err-06": ("Объем", "кг"),
        "err-07": ("Емкость аккумулятора", "В"),
    }
    by_id = {case["id"]: case for case in CASES}
    for case_id, (name, unit_text) in probes.items():
        case = by_id[case_id]
        assert "UNIT_MISMATCH" in case["expected_signals"]
        char = kb.families[case["expected_family"]].chars[char_key(name)]
        assert not U.same_dimension(U.parse_unit(unit_text), char.unit), case_id
