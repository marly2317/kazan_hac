"""Нормализация значений (архитектура §4.4): числа, единицы, строгие границы, тексты КТРУ."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from apps.ste.domain.kb import CharSpec, FamilyChar, char_key
from apps.ste.domain.model import Confidence, ExtractedValue, Source, Status
from apps.ste.domain.normalize import (
    compatible,
    display_value,
    has_content,
    map_text,
    normalize_value,
    option_value,
    parse_number,
    representative,
    split_items,
    split_number_unit,
    value_from_allowed,
)
from apps.ste.domain.values import AllowedValue

KB_DIR = Path(__file__).resolve().parents[2] / "kb"
GRINDER = "28.24.11.000-00000042"
MATTRESS = "31.03.10.000-00000005"
DESK = "31.01.10.000-00000006"


@pytest.fixture(scope="module")
def kb():
    if not (KB_DIR / "ktru_kb.json.gz").exists():
        pytest.skip("база знаний КТРУ не собрана")
    from apps.ste.kb_build.build import load

    return load(KB_DIR)


# --- конструкторы синтетики ----------------------------------------------------------------


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


def spec(name, allowed, type_=2, kind=2, required=False) -> CharSpec:
    return CharSpec(
        key=char_key(name),
        name=name,
        type=type_,
        kind=kind,
        required=required,
        allowed=tuple(allowed),
    )


def ev(value, unit=None, quote="цитата", status=Status.SPECIFIED, found=True, **extra):
    return ExtractedValue(
        char_key="x",
        status=status,
        value=value,
        unit=unit,
        quote=quote,
        quote_found=found,
        **extra,
    )


DISC = spec("Диаметр диска", [rng(115, 125), rng(125, 130), rng(130, 150)])
POWER = spec("Мощность", [rng(700, 800, "212"), rng(800, 900, "212"), rng(1100, 1200, "212")])
SUPPLY = spec(
    "Тип питания",
    [txt("От сети"), txt("От аккумулятора"), txt("Пневматический")],
    type_=1,
    required=True,
)
EXTRAS = spec(
    "Дополнительное оборудование",
    [txt("Полка для системного блока"), txt("Полка для клавиатуры"), txt("Замок, врезной")],
    type_=1,
    kind=3,
)


# --- parse_number / split_number_unit --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0,84", 0.84),
        ("1 200", 1200.0),
        ("1\u00a0200", 1200.0),
        ("1 200,5", 1200.5),
        ("2.03000", 2.03),
        ("125", 125.0),
        (" 125 ", 125.0),
        (3, 3.0),
        (0.5, 0.5),
        ("0", 0.0),
    ],
)
def test_parse_number_accepts(text, expected):
    assert parse_number(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text",
    ["1/2", "-5", -1.0, "", "  ", None, "abc", "12 34", "1.2.3", "125мм", True, math.nan, math.inf],
)
def test_parse_number_rejects(text):
    assert parse_number(text) is None


@pytest.mark.parametrize(
    ("text", "number", "unit"),
    [
        ("125мм", 125.0, "мм"),
        ("125 мм", 125.0, "мм"),
        ("0,84 кВт", 0.84, "кВт"),
        ("1 200 Вт", 1200.0, "Вт"),
        ("12", 12.0, None),
        ("1/2 дюйма", None, None),
        ("мм", None, None),
        ("", None, None),
        (None, None, None),
    ],
)
def test_split_number_unit(text, number, unit):
    assert split_number_unit(text) == (number, unit)


# --- сопоставление текста ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "options", "expected"),
    [
        ("от СЕТИ", ["От сети", "От аккумулятора"], "От сети"),
        ("«Тёплый»", ["Теплый", "Холодный"], "Теплый"),
        (
            "углошлифовальная машинка",
            ["Углошлифовальная машина", "Роторная машина"],
            "Углошлифовальная машина",
        ),
        ("аккумуляторный", ["От сети", "От аккумулятора"], "От аккумулятора"),
        ("мужская", ["Мужской", "Женский"], "Мужской"),
        ("женская", ["Мужской", "Женский"], "Женский"),
        ("курток", ["Куртка", "Брюки"], "Куртка"),
        ("пружиный", ["Пружинный", "Беспружинный", "Смешанный"], "Пружинный"),
        ("металлический", ["Металл", "Металл хромированный", "ЛДСП"], "Металл"),
        ("металл хромированный", ["Металл", "Металл хромированный"], "Металл хромированный"),
        (
            "IV климатический пояс",
            [
                "2 (III климатический пояс)",
                "3 (IV климатический пояс)",
                "4 (Особый климатический пояс)",
            ],
            "3 (IV климатический пояс)",
        ),
        ("пальто", ["Полупальто", "Пальто"], "Пальто"),
        ("синего цвета", ["Синий", "Красный"], "Синий"),
        ("без капюшона", ["С капюшоном", "Без капюшона"], "Без капюшона"),
        ("без капюшонов", ["С капюшоном", "Без капюшона"], "Без капюшона"),
        # Опечатка «приклеила» отрицание к соседнему слову — смысл тот же.
        (
            "Поворотныебез ускоренного хода",
            ["Поворотные с ускоренным ходом", "Поворотные без ускоренного хода"],
            "Поворотные без ускоренного хода",
        ),
        ("из кожи", ["Кожа", "Ткань"], "Кожа"),
    ],
)
def test_map_text_finds_ktru_value(value, options, expected):
    assert map_text(value, options) == expected


@pytest.mark.parametrize(
    ("value", "options"),
    [
        # Оба значения одинаково подходят — угадывать нельзя: уверенное значение исключит лист.
        ("металлический каркас с деревянной столешницей", ["Металлический", "Деревянный"]),
        # Общее слово «машина» не различает виды.
        ("машина", ["Углошлифовальная машина", "Роторная полировальная машина"]),
        # Половина слов значения — мало.
        ("углошлифовальная", ["Углошлифовальная машина", "Роторная машина"]),
        ("сетевой", ["От сети", "От аккумулятора"]),
        # Основа из 3 букв без падежного окончания — разные слова.
        ("сети", ["Сетка", "Ткань"]),
        # Совпали только числа — это не сопоставление.
        ("1 31/6", ["1", "1 3/16"]),
        # Отрицание переворачивает смысл.
        ("не металлический", ["Металлический", "Деревянный"]),
        ("без подкладки", ["С подкладкой", "Без утеплителя"]),
        ("", ["От сети"]),
        ("От сети", []),
        ("да", ["Нет"]),
    ],
)
def test_map_text_refuses_to_guess(value, options):
    assert map_text(value, options) is None


def test_split_items():
    assert split_items(["a", " b ", ""], multi=True) == ["a", "b"]
    assert split_items("a, b; c", multi=True) == ["a", "b", "c"]
    assert split_items("a, b", multi=False) == ["a, b"]
    assert split_items(None, multi=True) == []
    assert split_items(2.0, multi=False) == ["2"]


# --- числа: границы и единицы --------------------------------------------------------------------


def test_strict_and_inclusive_bounds():
    on_boundary = normalize_value(DISC, ev(125, "мм"))
    assert on_boundary.matched == ["≥125 <130 Миллиметр"]  # не «≥115 <125»: 125 там исключено
    assert normalize_value(DISC, ev(115, "мм")).matched == ["≥115 <125 Миллиметр"]
    assert normalize_value(DISC, ev(124.99, "мм")).matched == ["≥115 <125 Миллиметр"]
    assert normalize_value(DISC, ev(150, "мм")).matched == []  # «<150» исключает 150
    exclusive = spec("Высота", [rng(40, None, "004", low_inc=False)])
    assert normalize_value(exclusive, ev(40, "см")).matched == []
    assert normalize_value(exclusive, ev(40.5, "см")).matched == [">40 Сантиметр"]


def test_conversion_into_ktru_unit():
    nv = normalize_value(DISC, ev(12.5, "см", quote="12,5 см"))
    assert nv.number == 12.5
    assert nv.user_unit == "Сантиметр"
    assert nv.ktru_unit == "Миллиметр"
    assert nv.number_in_ktru_unit == 125
    assert nv.matched == ["≥125 <130 Миллиметр"]
    assert nv.unit_ok
    assert nv.was_unified
    assert nv.confidence == Confidence.CONFIDENT
    assert nv.user_text == "12,5 см"


def test_kilowatts_against_watt_ranges():
    nv = normalize_value(POWER, ev("0,84", "кВт"))
    assert nv.number_in_ktru_unit == 840
    assert nv.matched == ["≥800 <900 Ватт"]
    assert nv.confidence == Confidence.CONFIDENT
    # 1,1 кВт = 1100,0000000000002 Вт в двоичной арифметике — граница «≥1100» не должна потеряться.
    assert normalize_value(POWER, ev(1.1, "квт")).matched == ["≥1100 <1200 Ватт"]


def test_number_with_unit_in_one_string():
    nv = normalize_value(DISC, ev("125мм", None))
    assert nv.number == 125
    assert nv.user_unit == "Миллиметр"
    assert nv.matched == ["≥125 <130 Миллиметр"]


def test_missing_unit_assumes_ktru_unit():
    nv = normalize_value(DISC, ev(125))
    assert nv.user_unit is None
    assert nv.ktru_unit == "Миллиметр"
    assert nv.number_in_ktru_unit == 125
    assert nv.matched == ["≥125 <130 Миллиметр"]
    assert nv.confidence == Confidence.CONFIDENT
    assert not nv.was_unified
    # Без единицы и мимо всех интервалов — возможно, другая единица: только weak.
    lost = normalize_value(DISC, ev(12.5))
    assert lost.matched == []
    assert lost.unit_ok
    assert lost.confidence == Confidence.WEAK


def test_wrong_dimension_is_not_converted():
    nv = normalize_value(DISC, ev(5, "кг"))
    assert not nv.unit_ok
    assert nv.user_unit == "Килограмм"
    assert nv.number_in_ktru_unit is None
    assert nv.matched == []
    assert nv.confidence == Confidence.WEAK


def test_unknown_unit_is_kept_raw():
    nv = normalize_value(DISC, ev(125, "попугаев"))
    assert not nv.unit_ok
    assert nv.user_unit == "попугаев"
    assert nv.matched == []
    assert nv.confidence == Confidence.WEAK


def test_each_allowed_value_in_its_own_unit():
    mixed = spec("Длина", [rng(10, 20, "004"), rng(200, 300, "003")])
    assert normalize_value(mixed, ev(150, "мм")).matched == ["≥10 <20 Сантиметр"]
    assert normalize_value(mixed, ev(0.25, "м")).matched == ["≥200 <300 Миллиметр"]


def test_dimensionless_characteristic():
    rpm = spec("Количество оборотов в минуту", [rng(10000, 12000, None), rng(12000, 14000, None)])
    with_unit = normalize_value(rpm, ev(11000, "об/мин"))
    assert with_unit.unit_ok
    assert with_unit.ktru_unit is None
    assert with_unit.matched == ["≥10000 <12000"]
    assert with_unit.confidence == Confidence.CONFIDENT
    outside = normalize_value(rpm, ev(500, "об/мин"))
    assert outside.matched == []
    assert outside.confidence == Confidence.WEAK  # единицу проверить не с чем, число мимо


def test_overlapping_values_tightest_first():
    drawers = spec(
        "Количество выдвижных ящиков",
        [
            AllowedValue(kind="range", minimum=1, okei="796"),
            AllowedValue(kind="range", minimum=2, okei="796"),
            AllowedValue(kind="concrete", concrete=2, okei="796"),
        ],
    )
    nv = normalize_value(drawers, ev(2, "шт"))
    assert nv.matched == ["= 2 Штука", "≥2 Штука", "≥1 Штука"]


def test_unparseable_number():
    nv = normalize_value(DISC, ev("много"))
    assert nv.number is None
    assert nv.matched == []
    assert nv.confidence == Confidence.WEAK
    assert not has_content(nv)
    assert display_value(nv) == "цитата"


def test_number_list_takes_first_number():
    sizes = spec("Размер (штихмассовый)", [rng(38, 40, None), rng(40, 42, None)], kind=3)
    nv = normalize_value(sizes, ev(["?", "41", "39"]))
    assert nv.number == 41
    assert nv.matched == ["≥40 <42"]


# --- уверенность ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "found", "expected"),
    [
        (Status.SPECIFIED, True, Confidence.CONFIDENT),
        (Status.SPECIFIED, False, Confidence.WEAK),
        (Status.CONTRADICTION, True, Confidence.WEAK),
        (Status.ABSENT, True, Confidence.WEAK),
    ],
)
def test_confidence_rules(status, found, expected):
    assert normalize_value(DISC, ev(125, "мм", status=status, found=found)).confidence == expected
    assert normalize_value(SUPPLY, ev("От сети", status=status, found=found)).confidence == expected


def test_confirmed_stays_confirmed():
    edited = normalize_value(SUPPLY, ev("Титан", confidence=Confidence.CONFIRMED))
    assert edited.confidence == Confidence.CONFIRMED
    assert edited.texts == []
    answer = normalize_value(DISC, ev(5, "кг", source=Source.EDIT, confidence=Confidence.CONFIRMED))
    assert answer.confidence == Confidence.CONFIRMED
    assert answer.source == Source.EDIT
    assert not answer.unit_ok


def test_absent_value_has_no_content():
    nv = normalize_value(SUPPLY, ev(None, quote="что-то", status=Status.ABSENT))
    assert not has_content(nv)
    assert nv.confidence == Confidence.WEAK
    assert not has_content(normalize_value(DISC, ev(None, status=Status.ABSENT)))


def test_quote_not_found_uses_value_as_user_text():
    nv = normalize_value(SUPPLY, ev("аккумуляторный", quote="выдумано", found=False))
    assert nv.user_text == "аккумуляторный"
    assert nv.texts == ["От аккумулятора"]
    assert nv.confidence == Confidence.WEAK


# --- тексты -------------------------------------------------------------------------------------


def test_qualitative_mapped_and_unified():
    nv = normalize_value(SUPPLY, ev("аккумуляторный", quote="аккумуляторный"))
    assert nv.value_type == "text"
    assert nv.texts == ["От аккумулятора"]
    assert nv.matched == ["От аккумулятора"]
    assert nv.was_unified
    assert nv.confidence == Confidence.CONFIDENT


def test_qualitative_unmapped_is_weak_and_kept():
    nv = normalize_value(SUPPLY, ev("на солнечных батареях", quote="на солнечных батареях"))
    assert nv.texts == []
    assert nv.user_text == "на солнечных батареях"
    assert nv.confidence == Confidence.WEAK
    assert has_content(nv)


def test_multi_values():
    listed = normalize_value(EXTRAS, ev(["полка для клавиатуры", "Полка для системного блока"]))
    assert listed.value_type == "multi"
    assert listed.texts == ["Полка для клавиатуры", "Полка для системного блока"]
    assert listed.confidence == Confidence.CONFIDENT
    # Строкой через запятую — режем на элементы, иначе второе значение молча потерялось бы.
    joined = normalize_value(EXTRAS, ev("полка для клавиатуры; полка для системного блока"))
    assert joined.texts == ["Полка для клавиатуры", "Полка для системного блока"]
    # Значение КТРУ с запятой внутри — целиком.
    comma = normalize_value(EXTRAS, ev("замок, врезной"))
    assert comma.texts == ["Замок, врезной"]
    # Часть списка не из КТРУ — значение слабое.
    partial = normalize_value(EXTRAS, ev(["полка для клавиатуры", "подстаканник"]))
    assert partial.texts == ["Полка для клавиатуры"]
    assert partial.confidence == Confidence.WEAK


def test_family_char_spec_is_accepted():
    family_char = FamilyChar(
        key="вид",
        name="Вид",
        type=1,
        kinds=(1,),
        required=True,
        allowed=(txt("Куртка"), txt("Брюки")),
        leaf_count=2,
        differentiator=True,
    )
    nv = normalize_value(family_char, ev("курток"))
    assert nv.texts == ["Куртка"]
    assert nv.value_type == "text"


# --- совместимость с листом ----------------------------------------------------------------------


def test_compatible_qualitative():
    text = normalize_value(SUPPLY, ev("От сети"))
    assert compatible(None, text) is None
    assert compatible(spec("Тип питания", [txt("от сети")], type_=1, kind=1), text) is True
    assert compatible(spec("Тип питания", [txt("От аккумулятора")], type_=1), text) is False
    unmapped = normalize_value(SUPPLY, ev("солнечная батарея"))
    assert compatible(SUPPLY, unmapped) is False


def test_compatible_multi_requires_all():
    nv = normalize_value(EXTRAS, ev(["Полка для клавиатуры", "Полка для системного блока"]))
    both = spec("Доп", [txt("Полка для клавиатуры"), txt("Полка для системного блока")], 1, 3)
    one = spec("Доп", [txt("Полка для клавиатуры")], 1, 3)
    assert compatible(both, nv) is True
    assert compatible(one, nv) is False


def test_compatible_numeric_converts_per_leaf_value():
    nv = normalize_value(DISC, ev(125, "мм"))
    assert compatible(spec("Диаметр диска", [rng(12, 13, "004")]), nv) is True
    assert compatible(spec("Диаметр диска", [rng(115, 125)]), nv) is False
    assert compatible(spec("Диаметр диска", [rng(0.12, 0.13, "006")]), nv) is True


def test_compatible_degenerate_values():
    wrong_unit = normalize_value(DISC, ev(5, "кг"))
    assert compatible(DISC, wrong_unit) is False
    nothing = normalize_value(DISC, ev(None))
    assert compatible(DISC, nothing) is True
    assert compatible(spec("Пусто", []), normalize_value(DISC, ev(125, "мм"))) is True


# --- ответ кнопкой ------------------------------------------------------------------------------


def test_representative_inside_interval():
    assert representative(rng(125, 130)) == 127.5
    assert representative(AllowedValue(kind="range", minimum=16000)) == 16000
    open_low = AllowedValue(kind="range", maximum=300, max_inclusive=False)
    assert representative(open_low) < 300
    strict = AllowedValue(kind="range", minimum=40, min_inclusive=False)
    assert strict.contains_number(representative(strict))
    assert representative(AllowedValue(kind="concrete", concrete=2)) == 2


def test_option_value_round_trip():
    nv = option_value(DISC, "≥125 <130 Миллиметр")
    assert nv is not None
    assert nv.confidence == Confidence.CONFIRMED
    assert nv.source == Source.ANSWER
    assert nv.matched == ["≥125 <130 Миллиметр"]
    assert compatible(spec("Диаметр диска", [rng(125, 130)]), nv) is True
    assert compatible(spec("Диаметр диска", [rng(115, 125)]), nv) is False
    text = option_value(SUPPLY, "от сети")
    assert text is not None and text.texts == ["От сети"]
    assert option_value(SUPPLY, "дизель") is None
    ktru = value_from_allowed(SUPPLY, txt("От сети"), Source.KTRU)
    assert ktru.source == Source.KTRU and ktru.quote is None


# --- реальная база КТРУ ---------------------------------------------------------------------


def test_grinder_disc_125_lands_in_upper_band(kb):
    char = kb.families[GRINDER].chars["диаметр диска"]
    nv = normalize_value(char, ev(125, "мм", quote="125 мм"))
    assert nv.matched == ["≥125 <130 Миллиметр"]
    assert "≥115 <125 Миллиметр" not in nv.matched
    assert nv.confidence == Confidence.CONFIDENT


def test_grinder_power_kilowatts(kb):
    char = kb.families[GRINDER].chars["мощность"]
    nv = normalize_value(char, ev("0,84", "кВт", quote="0,84 кВт"))
    assert nv.number_in_ktru_unit == 840
    assert nv.matched == ["≥800 <900 Ватт"]
    assert nv.was_unified


def test_grinder_mass_for_length_is_unit_mismatch(kb):
    char = kb.families[GRINDER].chars["диаметр диска"]
    nv = normalize_value(char, ev(5, "кг", quote="5 кг"))
    assert not nv.unit_ok
    assert nv.confidence == Confidence.WEAK


def test_grinder_kind1_synonym_form(kb):
    char = kb.families[GRINDER].chars["тип питания"]
    nv = normalize_value(char, ev("аккумуляторная", quote="аккумуляторная"))
    assert nv.texts == ["От аккумулятора"]


def test_mattress_500_cm_out_of_every_interval(kb):
    char = kb.families[MATTRESS].chars["длина"]
    nv = normalize_value(char, ev(5, "метров", quote="длиной 5 метров"))
    assert nv.unit_ok
    assert nv.number_in_ktru_unit == 500
    assert nv.matched == []
    # Единица распознана и размерность совпала: значение уверенное — и именно оно даст конфликт.
    assert nv.confidence == Confidence.CONFIDENT


def test_desk_kind3_list(kb):
    char = kb.families[DESK].chars["дополнительное оборудование"]
    nv = normalize_value(char, ev("полка для клавиатуры, замок в выдвижном ящике"))
    assert nv.value_type == "multi"
    assert nv.texts == ["Полка для клавиатуры", "Замок в выдвижном ящике"]
    assert nv.confidence == Confidence.CONFIDENT
