from apps.ste.domain import units as U
from apps.ste.domain.values import AllowedValue


def test_strict_bounds():
    band = AllowedValue(
        kind="range", minimum=115, min_inclusive=True, maximum=125, max_inclusive=False, okei="003"
    )
    assert band.contains_number(115)
    assert not band.contains_number(125)
    assert band.describe() == "≥115 <125 Миллиметр"


def test_units():
    assert U.parse_unit("мм").code == "003"
    assert U.parse_unit("кВт").code == "214"
    assert U.convert(0.84, U.parse_unit("квт"), U.by_code("212")) == 840
    assert U.convert(5, U.parse_unit("кг"), U.by_code("003")) is None
    assert U.unit_in_name("Максимальный крутящий момент (Нм)").code == "x:Нм"
