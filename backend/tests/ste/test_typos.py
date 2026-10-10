"""Опечатки и раскладка по словарю КТРУ: исправить ошибку, не тронуть правильный ввод."""

from pathlib import Path

import pytest

from apps.ste.application.typos import TypoCorrector, is_inflection
from apps.ste.kb_build.build import load


@pytest.fixture(scope="module")
def corrector():
    return TypoCorrector(load(Path(__file__).resolve().parents[2] / "kb"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Кострукция сварная", "Конструкция сварная"),  # реальная ошибка из «Мебель.xlsx»
        ("малоток слесарный", "молоток слесарный"),
        ("шуруповрет аккумуляторный", "шуруповерт аккумуляторный"),
        ("матрац беспружиный", "матрац беспружинный"),
        ("шкаф для одеждыы", "шкаф для одежды"),
        ("vjkjnjr cktcfhysq", "молоток слесарный"),
    ],
)
def test_fixes_typos(corrector, text, expected):
    assert corrector.correct(text).corrected == expected


@pytest.mark.parametrize(
    "text",
    [
        "Болгарка Makita GA5030 125 мм",
        "стул офисный на колесиках черный",
        "куртка утеплённая мужская XL 48-50",
        "стол из ЛДСП 1400х700",
        "дрель-шуруповерт DeWALT 18В 2Ач",
        "крестовая отвертка PH2",
        "перфоратор SDS-plus 800 Вт",
    ],
)
def test_keeps_correct_input(corrector, text):
    result = corrector.correct(text)
    assert result.corrected == text
    assert result.fixes == []


def test_fix_offsets_point_into_original(corrector):
    text = "стол Кострукция сварная"
    fix = corrector.correct(text).fixes[0]
    assert text[fix.start : fix.end] == "Кострукция"


def test_inflection_is_not_a_typo():
    assert is_inflection("колесиках", "колесиков")
    assert not is_inflection("одеждыы", "одежды")
    assert not is_inflection("шуруповрет", "шуруповерт")
