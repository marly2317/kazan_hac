"""The КТРУ reference: parsing the organisers' XML, and unit normalisation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.ktru.domain.entities import PermittedValue, Unit
from apps.ktru.domain.enums import CharacteristicType, ValueKind
from apps.ktru.infrastructure.xml_parser import parse_positions

XML = """<?xml version="1.0" encoding="UTF-8"?>
<export xmlns="http://zakupki.gov.ru/oos/export/1"
        xmlns:oos="http://zakupki.gov.ru/oos/types/1"><nsiKTRUs>
<oos:position><oos:data>
  <oos:code>25.73.30.141-00000001</oos:code>
  <oos:name>Молоток</oos:name>
  <oos:status>ACTIVE</oos:status>
  <oos:OKPD2><oos:code>25.73.30.141</oos:code><oos:name>Инструмент ручной</oos:name></oos:OKPD2>
  <oos:characteristics>
    <oos:characteristic>
      <oos:code>c-1</oos:code><oos:name>Безынерционный</oos:name>
      <oos:type>1</oos:type><oos:actual>true</oos:actual><oos:isRequired>false</oos:isRequired>
      <oos:values>
        <oos:value><oos:valueCode>v1</oos:valueCode>
          <oos:qualityDescription>Да</oos:qualityDescription></oos:value>
        <oos:value><oos:valueCode>v2</oos:valueCode>
          <oos:qualityDescription>Нет</oos:qualityDescription></oos:value>
      </oos:values>
    </oos:characteristic>
    <oos:characteristic>
      <oos:code>c-2</oos:code><oos:name>Длина рукоятки</oos:name>
      <oos:type>2</oos:type><oos:actual>true</oos:actual><oos:isRequired>true</oos:isRequired>
      <oos:values>
        <oos:value><oos:valueCode>v3</oos:valueCode>
          <oos:OKEI><oos:code>003</oos:code><oos:name>Миллиметр</oos:name></oos:OKEI>
          <oos:rangeSet><oos:valueRange>
            <oos:minMathNotation>greater</oos:minMathNotation><oos:min>100</oos:min>
            <oos:maxMathNotation>lessOrEqual</oos:maxMathNotation><oos:max>150</oos:max>
          </oos:valueRange></oos:rangeSet></oos:value>
        <oos:value><oos:valueCode>v4</oos:valueCode>
          <oos:OKEI><oos:code>003</oos:code><oos:name>Миллиметр</oos:name></oos:OKEI>
          <oos:valueSet><oos:concreteValue>200</oos:concreteValue></oos:valueSet></oos:value>
      </oos:values>
    </oos:characteristic>
    <oos:characteristic>
      <oos:code>c-3</oos:code><oos:name>Снятая характеристика</oos:name>
      <oos:type>1</oos:type><oos:actual>false</oos:actual><oos:isRequired>false</oos:isRequired>
    </oos:characteristic>
  </oos:characteristics>
</oos:data></oos:position>
<oos:position><oos:data>
  <oos:code>25.73.30.141-00000002</oos:code>
  <oos:name>Отменённая позиция</oos:name>
  <oos:status>TERMINATED</oos:status>
</oos:data></oos:position>
</nsiKTRUs></export>
"""


@pytest.fixture
def dump(tmp_path):
    path = tmp_path / "nsiKTRUNew_all_actual_test.xml"
    path.write_text(XML, encoding="utf-8")
    return path


def test_only_active_positions_are_parsed(dump) -> None:
    positions = list(parse_positions(dump))

    # A TERMINATED position would hand the user a code they cannot actually use.
    assert [p.code for p in positions] == ["25.73.30.141-00000001"]


def test_position_carries_its_okpd2(dump) -> None:
    position = next(iter(parse_positions(dump)))

    assert position.name == "Молоток"
    assert position.okpd2_code == "25.73.30.141"
    assert position.okpd2_name == "Инструмент ручной"


def test_withdrawn_characteristics_are_skipped(dump) -> None:
    position = next(iter(parse_positions(dump)))

    assert [c.name for c in position.characteristics] == ["Безынерционный", "Длина рукоятки"]


def test_qualitative_values_are_read_as_texts(dump) -> None:
    characteristic = next(iter(parse_positions(dump))).characteristics[0]

    assert characteristic.type is CharacteristicType.QUALITATIVE
    assert not characteristic.is_required
    assert [v.text for v in characteristic.values] == ["Да", "Нет"]


def test_quantitative_values_keep_bounds_units_and_inclusivity(dump) -> None:
    characteristic = next(iter(parse_positions(dump))).characteristics[1]

    assert characteristic.is_required
    assert characteristic.units == [Unit(code="003", name="Миллиметр")]

    low, exact = characteristic.values
    assert low.kind is ValueKind.RANGE
    assert (low.minimum, low.maximum) == (Decimal("100"), Decimal("150"))
    # `greater` excludes the bound, `lessOrEqual` includes it — getting this backwards would
    # accept a value КТРУ rejects.
    assert low.min_inclusive is False
    assert low.max_inclusive is True

    assert exact.kind is ValueKind.EXACT
    assert exact.exact == Decimal("200")


@pytest.mark.parametrize(
    ("number", "allowed"),
    [("100", False), ("100.1", True), ("150", True), ("150.1", False)],
)
def test_range_membership_respects_exclusive_bounds(number: str, allowed: bool) -> None:
    value = PermittedValue(
        kind=ValueKind.RANGE,
        minimum=Decimal("100"),
        maximum=Decimal("150"),
        min_inclusive=False,
        max_inclusive=True,
    )

    assert value.contains(Decimal(number)) is allowed


def test_describe_reads_as_a_human_would_write_it() -> None:
    unit = Unit(code="003", name="Миллиметр")

    exact = PermittedValue(kind=ValueKind.EXACT, exact=Decimal("200.0"), unit=unit)
    assert exact.describe() == "200 Миллиметр"

    text = PermittedValue(kind=ValueKind.TEXT, text="Пластик")
    assert text.describe() == "Пластик"
