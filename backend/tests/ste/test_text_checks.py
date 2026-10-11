"""Проверки описания целиком на реальной базе знаний: тестовый набор организаторов."""

import pytest

from apps.ste import runtime
from apps.ste.application import text_checks as T


@pytest.fixture(scope="module")
def kb():
    return runtime.knowledge_base()


def _family(kb, name):
    return next(family for family in kb.selectable() if family.name == name)


def test_negative_values_and_ranges():
    assert [s.user_value for s in T.negative_value_signals("высота -750 мм, размер -52")] == [
        "-750 мм",
        "-52",
    ]
    assert T.negative_value_signals("патрон 1,5–13 мм, 1.5-13мм") == []


def test_several_products_in_one_position(kb):
    ids = [family.id for family in kb.selectable()]
    named = T.named_families(
        "Стол письменный для руководителя и стул на металлическом каркасе в одной позиции",
        kb,
        ids,
    )
    assert {family.name for family in named} == {"Стол письменный", "Стул на металлическом каркасе"}
    # Один товар с «и» в тексте — не несколько товаров.
    assert T.named_families("Брюки спортивные женские, ткань и трикотаж", kb, ids) == []


def test_contradiction_inside_text(kb):
    sofa = _family(kb, "Диван")
    [signal] = T.contradiction_signals("Диван прямой и угловой одновременно", sofa)
    assert set(signal.allowed) == {"Прямой", "Угловой"}
    trousers = _family(kb, "Брюки спортивные")
    assert T.contradiction_signals("брюки, ткань и трикотаж одновременно", trousers)
    assert (
        T.contradiction_signals("Мужские спортивные брюки из трикотажного полотна", trousers) == []
    )


def test_units_foreign_to_the_product(kb):
    suit = _family(kb, "Костюм спортивный")
    assert [s.user_value for s in T.unit_signals("размер 50, мощность двигателя 2 кВт", suit)] == [
        "2 кВт"
    ]
    table = _family(kb, "Стол компьютерный")
    assert T.unit_signals("2 ящика, 999 открытых полок", table)
    assert T.unit_signals("2 ящика, 3 открытые полки", table) == []
