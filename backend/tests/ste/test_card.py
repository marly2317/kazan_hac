"""Карточка СТЕ (архитектура §4.7): порядок, источники, унификация, дополнительные."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.ste.domain.card import ACTION_OPTIONAL, ACTION_REQUIRED, build_card
from apps.ste.domain.kb import CharSpec, Family, FamilyChar, Leaf, char_key
from apps.ste.domain.model import Confidence, ExtractedValue, Source, Status
from apps.ste.domain.narrowing import narrow
from apps.ste.domain.normalize import normalize_value, option_value
from apps.ste.domain.questions import fill_lists
from apps.ste.domain.values import AllowedValue

KB_DIR = Path(__file__).resolve().parents[2] / "kb"
GRINDER = "28.24.11.000-00000042"
DESK = "31.01.10.000-00000006"

CARD_KEYS = {"ktru_code", "ktru_name", "okpd2", "characteristics", "extra", "brand", "model"}
ROW_KEYS = {
    "key", "name", "value", "values", "user_value", "user_unit", "ktru_value", "unit", "source",
    "quote", "required", "kind", "confidence", "unified", "action", "options",
}  # fmt: skip


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


def family_char(item: CharSpec, kinds=None, differentiator=False) -> FamilyChar:
    return FamilyChar(
        key=item.key,
        name=item.name,
        type=item.type,
        kinds=kinds or (item.kind,),
        required=item.required,
        allowed=item.allowed,
        leaf_count=1,
        differentiator=differentiator,
    )


def ev(key, raw, unit=None, quote=None, confidence=Confidence.WEAK, source=Source.TEXT):
    return ExtractedValue(
        char_key=key,
        status=Status.SPECIFIED,
        value=raw,
        unit=unit,
        quote=quote if quote is not None else str(raw),
        quote_found=True,
        source=source,
        confidence=confidence,
    )


COLOR = spec("Цвет", [txt("Синий"), txt("Красный")])
KIND = spec("Вид", [txt("Куртка")], kind=1, required=True)
LENGTH = spec("Длина", [rng(600, 800), rng(800, 1000)], type_=2, required=True)
POCKETS = spec("Карманы", [AllowedValue(kind="concrete", concrete=2, okei="796")], type_=2)
EXTRAS = spec("Отделка", [txt("Кант"), txt("Вышивка"), txt("Тесьма")], kind=3)
CLOTH = spec("Ткань", [txt("Хлопок"), txt("Шерсть")], required=True)

LEAF = Leaf(
    "L1",
    "Куртка утеплённая",
    "F1",
    "14.12.30.160",
    {item.key: item for item in (COLOR, KIND, LENGTH, POCKETS, EXTRAS, CLOTH)},
)
FAMILY = Family(
    id="F1",
    short_id="F001",
    name="Одежда специальная",
    okpd2="14.12.30.160",
    okpd2_name="Средства защиты",
    category="одежда",
    role="family",
    leaves=["L1"],
    chars={
        COLOR.key: family_char(COLOR),
        KIND.key: family_char(KIND, differentiator=True),
        LENGTH.key: family_char(LENGTH),
        POCKETS.key: family_char(POCKETS),
        EXTRAS.key: family_char(EXTRAS),
        CLOTH.key: family_char(CLOTH),
    },
)


def _values():
    return {
        "длина": normalize_value(LENGTH, ev("длина", 70, "см", quote="70 см")),
        "отделка": normalize_value(EXTRAS, ev("отделка", ["кант", "вышивка"])),
        "цвет": normalize_value(COLOR, ev("цвет", "красного цвета", quote="красного цвета")),
    }


def _rows(card: dict) -> dict[str, dict]:
    return {row["key"]: row for row in card["characteristics"]}


def test_final_card_shape_and_order():
    values = _values()
    card = build_card(FAMILY, LEAF, values, fill_lists(LEAF, values), [], "Ivanovo", "")
    assert set(card) == CARD_KEYS
    assert card["ktru_code"] == "L1"
    assert card["ktru_name"] == "Куртка утеплённая"
    assert card["okpd2"] == {"code": "14.12.30.160", "name": "Средства защиты"}
    assert card["brand"] == "Ivanovo" and card["model"] is None
    assert all(set(row) == ROW_KEYS for row in card["characteristics"])
    # kind=1 → обязательные → остальные; внутри групп — порядок листа.
    assert [row["key"] for row in card["characteristics"]] == [
        "вид", "длина", "ткань", "цвет", "карманы", "отделка",
    ]  # fmt: skip
    json.dumps(card, ensure_ascii=False)


def test_numeric_row_shows_unification():
    values = _values()
    row = _rows(build_card(FAMILY, LEAF, values, fill_lists(LEAF, values), [], None, None))["длина"]
    assert row["value"] == "700"
    assert row["unit"] == "Миллиметр"
    assert row["user_value"] == "70"
    assert row["user_unit"] == "Сантиметр"
    assert row["ktru_value"] == "≥600 <800 Миллиметр"
    assert row["values"] == ["700"]
    assert row["source"] == "text"
    assert row["quote"] == "70 см"
    assert row["confidence"] == "confident"
    assert row["unified"] is True
    assert row["action"] is None
    assert row["required"] is True and row["kind"] == 2
    assert row["options"] == ["≥600 <800 Миллиметр", "≥800 <1000 Миллиметр"]


def test_text_rows():
    values = _values()
    rows = _rows(build_card(FAMILY, LEAF, values, fill_lists(LEAF, values), [], None, None))
    color = rows["цвет"]
    assert color["value"] == "Красный"
    assert color["user_value"] == "красного цвета"
    assert color["unified"] is True
    assert color["unit"] is None
    multi = rows["отделка"]
    assert multi["kind"] == 3
    assert multi["values"] == ["Кант", "Вышивка"]
    assert multi["value"] == "Кант, Вышивка"


def test_ktru_filled_and_actions():
    values = _values()
    rows = _rows(build_card(FAMILY, LEAF, values, fill_lists(LEAF, values), [], None, None))
    kind = rows["вид"]
    assert kind["source"] == "ktru"
    assert kind["value"] == "Куртка"
    assert kind["confidence"] is None
    assert kind["user_value"] is None
    assert kind["unified"] is False
    pockets = rows["карманы"]
    assert pockets["source"] == "ktru"
    assert pockets["value"] == "= 2 Штука"
    assert pockets["ktru_value"] == "= 2 Штука"
    cloth = rows["ткань"]
    assert cloth["source"] == "none"
    assert cloth["value"] is None
    assert cloth["action"] == ACTION_REQUIRED
    assert cloth["options"] == ["Хлопок", "Шерсть"]
    assert rows["отделка"]["action"] is None
    without_color = {key: nv for key, nv in values.items() if key != "цвет"}
    rows = _rows(build_card(FAMILY, LEAF, without_color, fill_lists(LEAF, without_color), [], None,
                            None))  # fmt: skip
    assert rows["цвет"]["action"] == ACTION_OPTIONAL


def test_exact_multi_values_are_not_unified():
    nv = normalize_value(EXTRAS, ev("отделка", "Кант; Вышивка", quote="Кант; Вышивка"))
    row = _rows(build_card(FAMILY, LEAF, {"отделка": nv}, None, [], None, None))["отделка"]
    assert row["unified"] is False


def test_unit_mismatch_row_is_not_unified():
    nv = normalize_value(LENGTH, ev("длина", 5, "кг", quote="5 кг"))
    row = _rows(build_card(FAMILY, LEAF, {"длина": nv}, None, [], None, None))["длина"]
    assert row["value"] is None
    assert row["user_value"] == "5"
    assert row["user_unit"] == "Килограмм"
    assert row["unit"] == "Миллиметр"
    assert row["unified"] is False
    assert row["confidence"] == "weak"


def test_unmapped_text_keeps_user_words():
    nv = normalize_value(CLOTH, ev("ткань", "кашемир"))
    row = _rows(build_card(FAMILY, LEAF, {"ткань": nv}, None, [], None, None))["ткань"]
    assert row["value"] == "кашемир"
    assert row["ktru_value"] is None
    assert row["confidence"] == "weak"


def test_confirmed_answer_row():
    nv = normalize_value(
        COLOR, ev("цвет", "Красный", confidence=Confidence.CONFIRMED, source=Source.ANSWER)
    )
    row = _rows(build_card(FAMILY, LEAF, {"цвет": nv}, None, [], None, None))["цвет"]
    assert row["source"] == "answer"
    assert row["confidence"] == "confirmed"
    assert row["unified"] is False


def test_interval_answer_row_shows_interval():
    answer = option_value(LENGTH, "≥800 <1000 Миллиметр")
    row = _rows(build_card(FAMILY, LEAF, {"длина": answer}, None, [], None, None))["длина"]
    assert row["value"] == "≥800 <1000 Миллиметр"  # не служебная точка 900
    assert row["user_value"] == "≥800 <1000 Миллиметр"
    assert row["ktru_value"] == "≥800 <1000 Миллиметр"
    assert row["source"] == "answer"
    assert row["confidence"] == "confirmed"
    assert row["unified"] is False


def test_family_card_before_final_leaf():
    values = {"длина": normalize_value(LENGTH, ev("длина", 70, "см"))}
    card = build_card(FAMILY, None, values, None, [], None, None)
    assert card["ktru_code"] is None
    assert card["ktru_name"] == "Одежда специальная"
    # Значение есть, различающая или обязательная — показываем; остальное — нет.
    assert [row["key"] for row in card["characteristics"]] == ["вид", "длина", "ткань"]
    assert all(row["action"] is None for row in card["characteristics"])


def test_extra_items_are_normalized():
    extra = [
        {"name": "напряжение", "value": 18.0, "unit": "В", "quote": "18В"},
        {"name": "подсветка", "value": "есть", "unit": None, "quote": "с подсветкой"},
        {"name": "Шум", "value": ["тихий", "очень"], "unit": "попугаев", "quote": "q"},
        {"name": "", "value": None, "unit": None, "quote": None},
        {"name": "Уровень шума", "value": "80", "unit": "дБ", "quote": "80 дБ",
         "matched_from": "громкость"},
    ]  # fmt: skip
    card = build_card(FAMILY, LEAF, {}, None, extra, None, None)
    assert card["extra"][:3] == [
        {"name": "Напряжение", "value": "18", "unit": "Вольт", "quote": "18В"},
        {"name": "Подсветка", "value": "есть", "unit": None, "quote": "с подсветкой"},
        {"name": "Шум", "value": "тихий, очень", "unit": "попугаев", "quote": "q"},
    ]
    assert len(card["extra"]) == 4
    assert card["extra"][3]["matched_from"] == "громкость"


def test_values_missing_in_final_leaf_move_to_extra():
    weight = spec("Вес", [rng(1, 5, "166")], type_=2)
    family = Family(**{**FAMILY.__dict__, "chars": {**FAMILY.chars, "вес": family_char(weight)}})
    values = {"вес": normalize_value(weight, ev("вес", 2000, "г", quote="2000 г"))}
    card = build_card(family, LEAF, values, None, [], None, None)
    assert "вес" not in _rows(card)
    assert card["extra"] == [{"name": "Вес", "value": "2", "unit": "Килограмм", "quote": "2000 г"}]


# --- реальная база КТРУ ------------------------------------------------------------------------


def test_grinder_card(kb):
    family = kb.families[GRINDER]
    raw = [
        ("вид", "Углошлифовальная машина", None, "болгарка"),
        ("диаметр диска", 125, "мм", "125 мм"),
        ("мощность", "0,84", "кВт", "0,84 кВт"),
    ]
    values = {
        key: normalize_value(family.chars[key], ev(key, value, unit, quote))
        for key, value, unit, quote in raw
    }
    [leaf] = narrow(kb.leaves_of(GRINDER), values).leaves
    card = build_card(family, leaf, values, fill_lists(leaf, values), [], "Makita", "GA5030")
    rows = _rows(card)
    assert card["ktru_code"] == "28.24.11.000-00000036"
    assert card["okpd2"]["name"] == "Инструменты ручные электрические"
    assert card["characteristics"][0]["key"] == "вид"
    assert rows["вид"]["user_value"] == "болгарка"
    assert rows["вид"]["value"] == "Углошлифовальная машина"
    assert rows["вид"]["unified"] is True
    assert rows["диаметр диска"]["ktru_value"] == "≥125 <130 Миллиметр"
    assert rows["мощность"]["value"] == "840"
    assert rows["мощность"]["ktru_value"] == "≥800 <900 Ватт"
    assert rows["тип питания"]["action"] == ACTION_REQUIRED
    assert rows["количество оборотов в минуту"]["action"] == ACTION_REQUIRED
    assert rows["емкость аккумулятора"]["action"] == ACTION_OPTIONAL
    json.dumps(card, ensure_ascii=False)


def test_desk_family_card_lists_required_and_differentiators(kb):
    family = kb.families[DESK]
    card = build_card(family, None, {}, None, [], None, None)
    keys = [row["key"] for row in card["characteristics"]]
    assert keys[0] == "тип каркаса"
    expected = {key for key, char in family.chars.items() if char.required or char.differentiator}
    assert set(keys) == expected
