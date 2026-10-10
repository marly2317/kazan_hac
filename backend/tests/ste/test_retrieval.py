"""Поиск семейств: карточки, очистка запроса, BM25, векторы, RRF, быстрый путь (§3.8, §3.9, §4.0).

Часть тестов — на синтетической базе (детерминированно), часть — замер качества на настоящей
базе знаний и наборе реалистичных запросов поставщика (:data:`QUERIES`). Векторные замеры
пропускаются, если нет посчитанных векторов или весов модели (CI).
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import numpy as np
import pytest

from apps.ste.application import retrieval as R
from apps.ste.domain import cards as C
from apps.ste.domain.kb import Family, FamilyChar, KnowledgeBase, char_key
from apps.ste.domain.model import FamilyHit
from apps.ste.domain.values import AllowedValue
from apps.ste.infrastructure import embedder as E
from apps.ste.kb_build import embed as V

# --- набор запросов поставщика --------------------------------------------------------------
#
# (запрос, ожидаемые семейства). Первое — основное; остальные — тоже верные (дубли КТРУ или
# честная неоднозначность: «куртка зимняя» без материала — и из текстиля, и из кожи).
# Пустой кортеж — товара в охвате нет (перфоратора в КТРУ-выгрузке нет, есть только бур к нему).

GRINDER = "28.24.11.000-00000042"  # Шлифмашина
SCREWDRIVER = "28.24.11.000-00000032"  # Шуруповерт
DRILL = "28.24.11.000-00000003"  # Дрель ручная электрическая
JIGSAW = "28.24.11.000-00000006"  # Электролобзик
HAMMER = "25.73.30.141-00000011"  # Молоток
SLEDGEHAMMER = "25.73.60.112-00000002"  # Кувалда
HAND_SAW = "25.73.20.110-00000009"  # Пила ручная
SABRE_SAW = "28.24.11.000-00000024"  # Пила сабельная
MITRE_SAW = "28.24.11.000-00000027"  # Пила торцовочная
HEAT_GUN = "28.24.11.000-00000021"  # Фен строительный
GLUE_GUN = "28.24.11.000-00000009"  # Пистолет клеевой электрический
PLIERS = "25.73.30.161-00000003"  # Плоскогубцы
COMBI_PLIERS = "25.73.30.163-00000019"  # Пассатижи
CUTTERS = "25.73.30.164-00000002"  # Кусачки
WRENCH = "25.73.30.171-00000003"  # Ключ гаечный разводной
VICE = "25.73.30.221-00000002"  # Тиски слесарные
CLAMP = "25.73.30.223-00000013"  # Струбцина
CHISEL = "25.73.30.210-00000004"  # Стамеска
E_PLANER = "28.24.11.000-00000034"  # Рубанок электрический
AXE = "25.73.10.000-00000013"  # Топор
EXTENSION = "25.73.60.190-00000008"  # Удлинитель электрический
SOLDERING = "25.73.30.293-00000004"  # Электропаяльник
RANGEFINDER = "25.73.30.000-00000004"  # Дальномер лазерный
ROUTER = "28.24.11.000-00000012"  # Фрезер ручной электрический
NAIL_PULLER = "25.73.30.290-00000002"  # Гвоздодер
SPATULA = "25.73.30.299-00000006"  # Шпатель
RENOVATOR = "25.73.60.190-00000005"  # Реноватор
STAPLER = "28.24.11.000-00000019"  # Степлер строительный электрический
DESK = "31.01.10.000-00000006"  # Стол письменный
PC_DESK = "31.09.10.000-00000037"  # Стол компьютерный
WARDROBE_WOOD = "31.01.12.131-00000003"  # Шкаф для одежды деревянный
WARDROBE_METAL = "31.01.11.121-00000004"  # Шкаф для одежды металлический
ARCHIVE = "31.01.11.122-00000002"  # Шкаф архивный металлический
CHAIR_METAL = "31.01.11.150-00000004"  # Стул на металлическом каркасе
CHAIR_WOOD = "31.01.12.160-00000004"  # Стул на деревянном каркасе
OFFICE_CHAIR = "31.01.12.160-00000006"  # Кресло офисное
MATTRESS = "31.03.10.000-00000005"  # Матрац
SOFA = "31.09.12.110-00000007"  # Диван
BED = "31.09.12.121-00000002"  # Кровать для взрослых бытового назначения
KIDS_BED = "31.09.10.000-00000040"  # Кровать детская
NIGHTSTAND = "31.09.12.124-00000003"  # Тумба прикроватная
RACK_STORE = "31.09.11.120-00000007"  # Стеллаж складской металлический
RACK_OFFICE = "31.01.10.000-00000009"  # Стеллаж офисный
STOOL = "31.09.10.000-00000019"  # Табурет
SCHOOL_DESK = "31.01.12.122-00000012"  # Парта
PUPIL_TABLE = "31.01.12.122-00000007"  # Стол ученический
HANGER = "31.09.11.120-00000005"  # Вешалка металлическая
WORKBENCH = "31.09.11.190-00000007"  # Верстак
IRONING = "31.09.11.190-00000011"  # Доска гладильная
COFFEE_TABLE = "31.09.12.132-00000004"  # Журнальный стол
BENCH = "31.09.13.190-00000022"  # Банкетка
TV_STAND = "31.09.13.190-00000015"  # Тумба под телевизор
JACKET_TEXTILE = "14.13.10.000-00000030"  # Куртка зимняя из текстильных материалов
JACKET_LEATHER = "14.11.10.000-00000006"  # Куртка зимняя из кожи
JACKET_FUR = "14.20.10.000-00000012"  # Куртка зимняя с верхом из меха
COLD_WORKWEAR = "14.12.30.160-00000340"  # Одежда специальная для защиты от пониженных температур
SANITARY_F = "14.12.20.000-00000005"  # Одежда санитарная женская
SANITARY_M = "14.12.10.000-00000005"  # Одежда санитарная мужская
WORK_ROBE = "14.12.30.130-00000003"  # Халат производственный
WORKWEAR = "14.12.30.160-00000321"  # Спецодежда от общих загрязнений и механических воздействий
TROUSERS = "14.13.12.140-00000003"  # Брюки
JEANS = "14.13.24.000-00000006"  # Джинсы
TSHIRT = "14.14.30.110-00000003"  # Футболка трикотажная
TSHIRT_KIDS = "14.14.30.110-00000006"  # Футболка трикотажная детская
HOODIE = "14.19.12.190-00000003"  # Толстовка
TRACKSUIT = "14.19.12.000-00000003"  # Костюм спортивный
WINTER_HAT = "14.19.40.000-00000007"  # Шапка зимняя
HEADWEAR = "14.19.40.000-00000020"  # Головные уборы
HI_VIS = "14.12.30.160-00000450"  # Одежда специальная повышенной видимости
GLOVES_KNIT = "14.12.30.150-00000004"  # Перчатки трикотажные для защиты
GLOVES_SEWN = "14.12.30.150-00000009"  # Перчатки швейные для защиты
MITTENS = "14.12.30.150-00000011"  # Рукавицы для защиты от внешних воздействий
DRESS = "14.13.14.140-00000004"  # Платье женское
PYJAMA_KIDS = "14.14.10.000-00000020"  # Пижама детская
JUMPER = "14.39.10.110-00000003"  # Джемпер
SHEEPSKIN = "14.20.10.350-00000005"  # Пальто нагольное (дубленка)
WINDBREAKER = "14.13.10.000-00000027"  # Ветровка
SCARF = "14.20.10.635-00000004"  # Шарф зимний
TIE = ("14.19.19.112-00000001", "14.19.19.112-00002")  # Галстук (дубль в КТРУ)
APRON = "14.12.30.130-00000007"  # Фартук профессиональный
SNEAKERS = "15.20.21.140-00000006"  # Кроссовки
KEDS = "15.20.21.140-00000003"  # Кеды
WINTER_SHOES = "15.20.13.140-00000025"  # Обувь зимняя с верхом из кожи
SAFETY_SHOES = "15.20.31.000-00000015"  # Обувь с защитным металлическим подноском
RUBBER_BOOTS = "15.20.11.113-00000015"  # Сапоги резиновые формовые
FELT_BOOTS = "15.20.14.130-00000010"  # Обувь валяная грубошерстная
CASUAL_SHOES = "15.20.13.110-00000001"  # Обувь повседневная с верхом из кожи
SUMMER_SHOES = "15.20.13.130-00000033"  # Обувь летняя с верхом из кожи
SEASON_SHOES = "15.20.13.150-00000025"  # Обувь весенне-осенняя
INSOLE = "15.20.40.120-00013"  # Стелька для обуви
BACKPACK = "15.12.12.192-00000002"  # Рюкзак туристический
SCHOOL_BAG = "15.12.12.191-00000002"  # Ранец школьный
SPORT_BAG = "15.12.12.192-00000004"  # Сумка спортивная

LEATHER_SHOES = (CASUAL_SHOES, WINTER_SHOES, SEASON_SHOES, SUMMER_SHOES)

QUERIES: list[tuple[str, tuple[str, ...]]] = [
    # инструменты
    ("болгарка Makita GA5030 125мм 840 Вт", (GRINDER,)),
    ("УШМ 125 мм с регулировкой оборотов", (GRINDER,)),
    ("Углошлифовальная машина 230 мм, 2000 Вт", (GRINDER,)),
    ("шлифмашинка эксцентриковая 125мм", (GRINDER,)),
    ("шуруповерт аккумуляторный 18В 2 Ач", (SCREWDRIVER,)),
    ("Шуруповёрт сетевой Интерскол", (SCREWDRIVER,)),
    ("дрель ударная 750 Вт", (DRILL,)),
    ("дрель-шуруповерт аккумуляторная Bosch PSR 14.4", (DRILL, SCREWDRIVER)),
    ("электролобзик 650 Вт с маятниковым ходом", (JIGSAW,)),
    ("лобзик электрический по дереву", (JIGSAW,)),
    ("молоток столярный 500 г", (HAMMER,)),
    ("кувалда 5 кг", (SLEDGEHAMMER,)),
    ("ножовка по дереву 450 мм", (HAND_SAW,)),
    ("пила сабельная аккумуляторная", (SABRE_SAW,)),
    ("торцовочная пила 1800 Вт диск 255 мм", (MITRE_SAW,)),
    ("фен строительный 2000 Вт", (HEAT_GUN,)),
    ("термопистолет клеевой", (GLUE_GUN,)),
    ("плоскогубцы комбинированные 180 мм", (PLIERS, COMBI_PLIERS)),
    ("пассатижи диэлектрические 1000В", (COMBI_PLIERS, PLIERS)),
    ("кусачки боковые", (CUTTERS,)),
    ("ключ разводной 250 мм", (WRENCH,)),
    ("тиски слесарные 125мм", (VICE,)),
    ("струбцина F-образная", (CLAMP,)),
    ("стамеска плоская 20 мм", (CHISEL,)),
    ("рубанок электрический 900 Вт", (E_PLANER,)),
    ("топор колун 2 кг", (AXE,)),
    ("удлинитель на катушке 50 м 4 розетки", (EXTENSION,)),
    ("паяльник 40 Вт", (SOLDERING,)),
    ("лазерный дальномер 50 м", (RANGEFINDER,)),
    ("фрезер ручной 1200 Вт", (ROUTER,)),
    ("гвоздодер 600 мм", (NAIL_PULLER,)),
    ("шпатель 100 мм нержавейка", (SPATULA,)),
    ("реноватор многофункциональный инструмент", (RENOVATOR,)),
    ("степлер строительный электрический", (STAPLER,)),
    # мебель
    ("стол письменный 1200х600 ЛДСП", (DESK,)),
    ("стол офисный для руководителя", (DESK,)),
    ("стол компьютерный угловой", (PC_DESK,)),
    ("шкаф для одежды двухстворчатый", (WARDROBE_WOOD, WARDROBE_METAL)),
    ("шкаф металлический для одежды в раздевалку", (WARDROBE_METAL,)),
    ("шкаф архивный металлический", (ARCHIVE,)),
    ("стул офисный на металлическом каркасе", (CHAIR_METAL,)),
    ("стул деревянный", (CHAIR_WOOD,)),
    ("кресло офисное с подлокотниками", (OFFICE_CHAIR,)),
    ("кресло руководителя кожаное", (OFFICE_CHAIR,)),
    ("компьютерное кресло на колесиках", (OFFICE_CHAIR,)),
    ("матрас ортопедический 160х200", (MATTRESS,)),
    ("матрас пружинный", (MATTRESS,)),
    ("диван трехместный", (SOFA,)),
    ("кровать односпальная деревянная", (BED,)),
    ("кровать детская", (KIDS_BED,)),
    ("тумба прикроватная", (NIGHTSTAND,)),
    ("стеллаж металлический складской", (RACK_STORE, RACK_OFFICE)),
    ("табуретка", (STOOL,)),
    ("парта школьная двухместная", (SCHOOL_DESK, PUPIL_TABLE)),
    ("вешалка напольная", (HANGER,)),
    ("верстак слесарный", (WORKBENCH,)),
    ("гладильная доска", (IRONING,)),
    ("журнальный столик", (COFFEE_TABLE,)),
    ("тумба под телевизор", (TV_STAND,)),
    ("банкетка", (BENCH,)),
    # одежда
    ("куртка зимняя мужская", (JACKET_TEXTILE, JACKET_LEATHER, JACKET_FUR)),
    ("пуховик женский", (JACKET_TEXTILE,)),
    ("спецодежда утеплённая, костюм зимний рабочий", (COLD_WORKWEAR,)),
    ("костюм рабочий утепленный для IV климатического пояса", (COLD_WORKWEAR,)),
    ("халат медицинский женский", (SANITARY_F, WORK_ROBE)),
    ("брюки рабочие", (WORKWEAR, TROUSERS)),
    ("брюки мужские классические", (TROUSERS,)),
    ("джинсы мужские", (JEANS,)),
    ("футболка хлопковая", (TSHIRT,)),
    ("футболка детская", (TSHIRT_KIDS,)),
    ("толстовка с капюшоном", (HOODIE,)),
    ("костюм спортивный мужской", (TRACKSUIT,)),
    ("шапка зимняя вязаная", (WINTER_HAT,)),
    ("бейсболка", (HEADWEAR,)),
    ("сигнальный жилет светоотражающий", (HI_VIS,)),
    ("перчатки рабочие х/б", (GLOVES_KNIT, GLOVES_SEWN)),
    ("рукавицы брезентовые", (MITTENS,)),
    ("платье женское трикотажное", (DRESS,)),
    ("пижама детская", (PYJAMA_KIDS,)),
    ("свитер шерстяной", (JUMPER,)),
    ("дубленка женская", (SHEEPSKIN,)),
    ("ветровка", (WINDBREAKER,)),
    ("шарф", (SCARF,)),
    ("галстук", TIE),
    ("фартук", (APRON,)),
    # обувь, сумки
    ("кроссовки мужские", (SNEAKERS,)),
    ("кеды", (KEDS,)),
    ("ботинки зимние кожаные", (WINTER_SHOES,)),
    ("ботинки рабочие с металлическим подноском", (SAFETY_SHOES,)),
    ("сапоги резиновые", (RUBBER_BOOTS,)),
    ("валенки", (FELT_BOOTS,)),
    ("полуботинки мужские кожаные", LEATHER_SHOES),
    ("туфли женские", LEATHER_SHOES),
    ("стельки для обуви", (INSOLE,)),
    ("рюкзак туристический 60 л", (BACKPACK,)),
    ("рюкзак", (BACKPACK, SCHOOL_BAG)),
    ("сумка спортивная", (SPORT_BAG,)),
    ("ранец школьный", (SCHOOL_BAG,)),
    # нет в охвате
    ("перфоратор SDS-plus 800 Вт", ()),
]


# --- общие заготовки ---------------------------------------------------------------------------

KB_DIR = Path(__file__).resolve().parents[2] / "kb"


def _char(name: str, values: list[str], kinds=(1,), differentiator=True) -> FamilyChar:
    return FamilyChar(
        key=char_key(name),
        name=name,
        type=1,
        kinds=tuple(kinds),
        required=True,
        allowed=tuple(AllowedValue(kind="text", text=value) for value in values),
        leaf_count=2,
        differentiator=differentiator,
    )


def _family(
    code: str,
    short_id: str,
    name: str,
    *,
    okpd2: str = "28.24.11.000",
    okpd2_name: str = "Инструменты ручные электрические",
    category: str = "инструменты",
    role: str = "family",
    chars: list[FamilyChar] | None = None,
    description: str | None = None,
) -> Family:
    return Family(
        id=code,
        short_id=short_id,
        name=name,
        okpd2=okpd2,
        okpd2_name=okpd2_name,
        category=category,
        role=role,
        leaves=[code],
        chars={char.key: char for char in chars or []},
        description=description,
    )


def _mini_kb() -> KnowledgeBase:
    """Пять семейств и одна общая позиция — хватает, чтобы проверить ранжирование руками."""
    families = [
        _family(
            "F-GRIND",
            "F001",
            "Шлифмашина",
            chars=[
                _char("Вид", ["Углошлифовальная машина", "Эксцентриковая (орбитальная)"]),
                _char("Тип питания", ["От сети", "От аккумулятора"], kinds=(2,)),
            ],
        ),
        _family(
            "F-SCREW",
            "F002",
            "Шуруповерт",
            chars=[_char("Тип питания", ["От сети", "От аккумуляторных батарей"])],
        ),
        _family(
            "F-DRILL",
            "F003",
            "Дрель ручная электрическая",
            chars=[_char("Тип питания", ["От сети", "От аккумулятора"])],
        ),
        _family(
            "F-DESK",
            "F004",
            "Стол письменный",
            okpd2="31.01.1",
            okpd2_name="Мебель для офисов и предприятий торговли",
            category="мебель",
            chars=[_char("Тип каркаса", ["Металлический", "Деревянный"])],
        ),
        _family(
            "F-SOLDER",
            "F005",
            "Электропаяльник",
            okpd2="25.73.30.293",
            okpd2_name="Лампы паяльные",
            chars=[_char("Режим работы", ["Непрерывный", "Импульсный"])],
        ),
        _family(
            "F-OTHER",
            "F006",
            "Инструменты ручные электрические прочие",
            okpd2="28.24.10",
            okpd2_name="Инструменты ручные электрические прочие",
            role="generic",
        ),
    ]
    return KnowledgeBase(
        families={family.id: family for family in families},
        leaves={},
        position_family={},
        scope=["28.24"],
    )


class FakeEmbedder:
    """Эмбеддер-заглушка: вектор запроса берётся из словаря, неизвестный текст — ноль."""

    model_name = "fake-model"
    enabled = True

    def __init__(self, table: dict[str, list[float]], dim: int = 3) -> None:
        self.table = table
        self.dim = dim
        self.calls: list[str] = []

    def _vector(self, text: str) -> np.ndarray:
        vector = np.array(self.table.get(text, [0.0] * self.dim), dtype=np.float32)
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector

    def encode_queries(self, texts):
        self.calls.extend(texts)
        return np.stack([self._vector(text) for text in texts])

    def encode_docs(self, texts):
        return np.stack([self._vector(text) for text in texts])


def _vectors(rows: dict[str, list[float]], model: str = "fake-model") -> V.FamilyVectors:
    matrix = np.array(list(rows.values()), dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    return V.FamilyVectors(ids=list(rows), matrix=matrix, model=model)


# --- карточки ----------------------------------------------------------------------------------


def test_okpd2_group_trims_zero_tail():
    assert C.okpd2_group("28.24.11.000") == "28.24.11"
    assert C.okpd2_group("31.01.1") == "31.01.1"
    assert C.okpd2_group("14.12.30.160") == "14.12.30.160"


def test_short_card_layout_and_limits():
    kinds = [f"Вид {index}" for index in range(12)] + ["Да", "Нет", "44-46"]
    family = _family(
        "X",
        "F042",
        "Шлифмашина",
        chars=[_char("Половой признак", ["Мужской"]), _char("Вид", kinds)],
    )
    card = C.short_card(family)
    assert card.startswith("[F042] Шлифмашина | ОКПД2 28.24.11 Инструменты ручные электрические")
    listed = card.split("виды: ")[1].split("; ")
    # «Вид» раньше «Полового признака», не больше 10, без «Да/Нет» и размеров.
    assert listed[0] == "Вид 0" and len(listed) == C.SHORT_KINDS
    assert "Да" not in listed and "44-46" not in listed and "Мужской" not in listed


def test_short_card_uses_llm_synonyms():
    family = _family("X", "F042", "Шлифмашина")
    family.synonyms = ["болгарка", "УШМ", "Болгарка", "углошлифовалка"]
    card = C.short_card(family)
    assert card.startswith("[F042] Шлифмашина | синонимы: болгарка, УШМ, углошлифовалка | ОКПД2")


def test_short_card_clips_long_okpd2_name_by_word():
    family = _family("X", "F001", "Шапочка", okpd2_name="Средства защиты " * 10)
    okpd2 = C.short_card(family).split(" | ")[1]
    assert okpd2.endswith("…") and len(okpd2) <= len("ОКПД2 28.24.11 ") + C.SHORT_OKPD2_NAME + 1
    assert "защит…" not in okpd2  # обрезано по границе слова


def test_full_card_contains_everything_searchable():
    family = _family(
        "X",
        "F013",
        "Одежда санитарная женская",
        chars=[
            _char(
                "Вид санитарной одежды", ["Халат", "Брюки", "Да"], kinds=(3,), differentiator=False
            ),
            _char("Рост", [], kinds=(2,), differentiator=False),
        ],
        description="Описание из КТРУ. " * 40,
    )
    family.keywords = ["медицина", "санитария"]
    card = C.full_card(family)
    assert card.splitlines()[0] == "[F013] Одежда санитарная женская"
    assert "варианты: Халат; Брюки\n" in card
    assert "характеристики: Вид санитарной одежды, Рост" in card
    assert "ключевые слова: медицина, санитария" in card
    about = next(line for line in card.splitlines() if line.startswith("что это: "))
    assert about.endswith("…") and len(about) <= len("что это: ") + C.FULL_DESCRIPTION + 1
    # Описание из метаданных важнее описания позиции КТРУ.
    family.summary = "Одежда медицинского персонала."
    assert "что это: Одежда медицинского персонала." in C.full_card(family)


def test_full_card_kinds_limit_is_30():
    family = _family("X", "F001", "Одежда", chars=[_char("Вид", [f"В{i}" for i in range(40)])])
    kinds_line = next(line for line in C.full_card(family).splitlines() if line.startswith("виды"))
    assert len(kinds_line.removeprefix("виды: ").split("; ")) == C.FULL_KINDS


def test_generic_card_does_not_repeat_okpd2_name():
    family = _mini_kb().families["F-OTHER"]
    assert (
        C.generic_card(family) == "[F006] Инструменты ручные электрические прочие | ОКПД2 28.24.10"
    )
    assert "общая позиция" in C.full_card(family)


@pytest.fixture(scope="module")
def real_kb():
    if not (KB_DIR / "ktru_kb.json.gz").exists():
        pytest.skip("база знаний КТРУ не собрана")
    from apps.ste.kb_build.build import load

    return load(KB_DIR)


def test_short_cards_fit_llm1_prompt(real_kb):
    """Весь список карточек — в бюджете промпта LLM-1 (≈ 4 символа на токен).

    С синонимами из LLM-метаданных карточка длиннее, поэтому бюджет с запасом на них:
    ~15 тыс. токенов на весь охват в режиме ``full_list``; в ``top_k`` — 30 карточек.
    """
    cards = [C.short_card(family) for family in real_kb.selectable()]
    lengths = [len(card) for card in cards]
    # Весь список (режим full_list) — ~25 тыс. токенов при окне Gemma 4 в 262 тыс.
    assert sum(lengths) < 80_000
    assert statistics.median(lengths) < 250 and max(lengths) < 520
    grinder = C.short_card(real_kb.families[GRINDER])
    assert grinder.startswith("[F")
    assert "Шлифмашина |" in grinder and "болгарка" in grinder
    assert "ОКПД2 28.24.11 Инструменты ручные электрические | виды:" in grinder
    assert "Углошлифовальная машина" in grinder


# --- очистка запроса и анализ ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("болгарка Makita GA5030 125мм 840 Вт", "болгарка"),
        ("Шуруповёрт 18 В 2 Ач Интерскол", "шуруповерт"),
        ("стол 1200х600 мм, ЛДСП", "стол лдсп"),
        ("перфоратор SDS-plus 800 Вт", "перфоратор"),
        ("кабель 5 м в бухте", "кабель в бухте"),  # «в» не после числа — не единица
        ("cтол письменный", "стол письменный"),  # латинская «c» в кириллическом слове
        ("Makita GA5030", ""),
        ("", ""),
        ("  125  ", ""),
    ],
)
def test_clean_query(text, expected):
    assert R.clean_query(text, brands=["интерскол"]) == expected


def test_analyze_morphology():
    assert R.analyze("Шуруповёрты") == R.analyze("шуруповерт")
    # Прилагательное и существительное сходятся благодаря обрезке основы.
    assert R.analyze("аккумуляторный") == R.analyze("от аккумулятора")
    assert R.analyze("табуретка") == R.analyze("табурет")
    # Сложное слово даёт и целый терм, и терм без приставки.
    assert set(R.analyze("паяльник")) <= set(R.analyze("Электропаяльник"))
    assert R.analyze("для и в на") == []


def test_bm25_basics():
    bm25 = R.Bm25([["стол", "стол", "письмен"], ["стол"] + ["шум"] * 20, ["шкаф"]])
    scores = bm25.scores(["стол", "нет-такого"])
    assert scores[0] > scores[1] > 0 and scores[2] == 0
    assert all(value > 0 for value in bm25.idf.values())
    assert bm25.scores([]).sum() == 0
    # Повтор слова в запросе не удваивает вклад.
    assert np.allclose(bm25.scores(["стол", "стол"]), bm25.scores(["стол"]))


# --- поиск: только лексика -------------------------------------------------------------------


def test_bm25_only_search():
    retriever = R.Retriever(_mini_kb(), brands=[])
    hits = retriever.search("Шуруповерт аккумуляторный 18В", k=3)
    assert hits[0].family_id == "F-SCREW" and hits[0].short_id == "F002"
    assert hits[0].score == 1.0 and hits[0].bm25_rank == 1
    assert hits[0].vector_rank is None and hits[0].cosine is None
    assert len(hits) <= 3
    assert not retriever.has_vectors


def test_search_compound_word_and_generic_filter():
    retriever = R.Retriever(_mini_kb(), brands=[])
    assert retriever.search("паяльник 40 Вт")[0].family_id == "F-SOLDER"
    plain = retriever.search("инструменты ручные электрические прочие")
    assert "F-OTHER" not in [hit.family_id for hit in plain]
    with_generic = retriever.search("инструменты ручные электрические прочие", include_generic=True)
    assert with_generic[0].family_id == "F-OTHER"


@pytest.mark.parametrize("text", ["", "   ", "125 мм 840 Вт", "Makita GA5030", "для и в"])
def test_search_without_product_words_is_empty(text):
    assert R.Retriever(_mini_kb(), brands=[]).search(text) == []


def test_search_timed_reports_steps():
    hits, timings = R.Retriever(_mini_kb(), brands=[]).search_timed("стол письменный")
    assert hits and set(timings) == {"clean", "bm25", "vector", "fuse", "total"}


# --- поиск: векторы и RRF ---------------------------------------------------------------------

#: Оси векторов: 0 — шлифование, 1 — закручивание, 2 — мебель.
_DOC_VECTORS = {
    "F-GRIND": [1.0, 0.1, 0.0],
    "F-SCREW": [0.1, 1.0, 0.0],
    "F-DRILL": [0.3, 0.8, 0.0],
    "F-DESK": [0.0, 0.0, 1.0],
    "F-SOLDER": [0.2, 0.2, 0.1],
    "F-OTHER": [0.5, 0.5, 0.0],
}


def _hybrid(table: dict[str, list[float]]) -> tuple[R.Retriever, FakeEmbedder]:
    embedder = FakeEmbedder(table)
    return R.Retriever(_mini_kb(), embedder, _vectors(_DOC_VECTORS), brands=[]), embedder


def test_vector_finds_what_bm25_cannot():
    retriever, _ = _hybrid({"болгарка": [1.0, 0.0, 0.0]})
    hits = retriever.search("болгарка Makita 125 мм")
    top = hits[0]
    assert top.family_id == "F-GRIND" and top.bm25_rank is None and top.vector_rank == 1
    assert top.cosine == pytest.approx(0.995, abs=0.01)
    # BM25 не нашёл ничего: вектор — единственный голос, и его первое место — это 1.
    assert top.score == pytest.approx(1.0)


def test_rrf_agreement_scores_one_and_ranks_are_consistent():
    retriever, _ = _hybrid({"шуруповерт": [0.1, 1.0, 0.0]})
    hits = retriever.search("шуруповерт", k=10)
    assert hits[0].family_id == "F-SCREW" and hits[0].score == 1.0
    assert hits[0].bm25_rank == 1 and hits[0].vector_rank == 1
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)
    # Вектор ранжирует все семейства (кроме общих) — поэтому все они в выдаче.
    assert {hit.family_id for hit in hits} == set(_DOC_VECTORS) - {"F-OTHER"}


def test_query_vector_is_cached():
    retriever, embedder = _hybrid({"шуруповерт": [0.1, 1.0, 0.0]})
    retriever.search("шуруповерт 18В")
    retriever.search("Шуруповерт 12 В")
    assert embedder.calls == ["шуруповерт"]


def test_vectors_of_other_model_are_ignored():
    vectors = _vectors(_DOC_VECTORS, model="other-model")
    retriever = R.Retriever(_mini_kb(), FakeEmbedder({}), vectors, brands=[])
    assert not retriever.has_vectors
    assert retriever.search("шуруповерт")[0].cosine is None


def test_dimension_mismatch_falls_back_to_bm25():
    embedder = FakeEmbedder({"шуруповерт": [1.0, 0.0, 0.0, 0.0]}, dim=4)
    retriever = R.Retriever(_mini_kb(), embedder, _vectors(_DOC_VECTORS), brands=[])
    hits = retriever.search("шуруповерт")
    assert hits[0].family_id == "F-SCREW" and hits[0].cosine is None
    assert not retriever.has_vectors


def test_families_without_vectors_still_searchable():
    partial = {key: value for key, value in _DOC_VECTORS.items() if key != "F-DESK"}
    embedder = FakeEmbedder({"стол письменный": [0.0, 0.0, 1.0]})
    retriever = R.Retriever(_mini_kb(), embedder, _vectors(partial), brands=[])
    top = retriever.search("стол письменный")[0]
    assert top.family_id == "F-DESK" and top.cosine is None and top.vector_rank is None


# --- уверенность (быстрый путь) --------------------------------------------------------------


def test_confidence_strong_when_everything_agrees():
    retriever, _ = _hybrid({"шуруповерт": [0.1, 1.0, 0.0]})
    hits = retriever.search("шуруповерт")
    features = retriever.confidence(hits)
    assert features["agree"] and features["name_overlap"] == 1.0
    assert features["cosine_margin"] > R.STRONG_COSINE_MARGIN
    assert features["strong"] is True
    # Пайплайн копирует список (list(...)) — признаки находятся по самим объектам попаданий.
    assert retriever.confidence(list(hits))["strong"] is True


def test_confidence_not_strong_on_disagreement_or_partial_name():
    retriever, _ = _hybrid({"дрель": [0.1, 1.0, 0.0]})  # вектор тянет к шуруповерту
    features = retriever.confidence(retriever.search("дрель"))
    assert features["top1_id"] == "F-DRILL" and not features["agree"]
    assert features["name_overlap"] == pytest.approx(1 / 3, abs=0.01)
    assert features["strong"] is False


def test_confidence_bm25_only_requires_full_name_and_margin():
    retriever = R.Retriever(_mini_kb(), brands=[])
    assert retriever.confidence(retriever.search("шуруповерт"))["strong"] is True
    weak = retriever.confidence(retriever.search("от сети"))  # совпадает у трёх семейств
    assert weak["strong"] is False


def test_confidence_on_foreign_hits_and_explicit_text():
    retriever = R.Retriever(_mini_kb(), brands=[])
    assert retriever.confidence([])["strong"] is False
    foreign = [FamilyHit("F-DESK", "F004", "Стол письменный", 1.0, bm25_rank=1)]
    features = retriever.confidence(foreign)
    assert features["name_overlap"] is None and features["strong"] is False
    assert retriever.confidence(foreign, text="стол письменный")["name_overlap"] == 1.0


# --- эмбеддер --------------------------------------------------------------------------------


def test_embedder_disabled_without_model(tmp_path):
    missing = E.Embedder(tmp_path / "нет-модели", True)
    assert not missing.enabled and not missing.available
    assert missing.encode_queries(["болгарка"]) is None and missing.dimension is None
    off = E.Embedder(tmp_path, False)
    assert off.encode_docs(["x"]) is None
    assert E.Embedder(None).encode_queries(["x"]) is None


class _FakeSentenceTransformer:
    instances = 0

    def __init__(self, path, device=None):
        type(self).instances += 1
        self.max_seq_length = 32768
        self.seen: list[str] = []

    def get_sentence_embedding_dimension(self):
        return 2

    def encode(self, texts, **kwargs):
        assert kwargs["normalize_embeddings"] is True
        self.seen.extend(texts)
        return np.array([[3.0, 4.0]] * len(texts), dtype=np.float64) / 5.0


def test_embedder_prefixes_queries_only(tmp_path, monkeypatch):
    import sentence_transformers

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", _FakeSentenceTransformer)
    embedder = E.Embedder(tmp_path, True, query_instruction="Instruct: X\nQuery: ")
    queries = embedder.encode_queries(["болгарка"])
    docs = embedder.encode_docs(["[F001] Шлифмашина"])
    assert queries.dtype == np.float32 and queries.shape == (1, 2)
    assert np.allclose(np.linalg.norm(docs, axis=1), 1.0)
    assert embedder._model.seen == ["Instruct: X\nQuery: болгарка", "[F001] Шлифмашина"]
    assert embedder._model.max_seq_length == E.MAX_SEQ_LENGTH
    assert embedder.dimension == 2 and embedder.model_name == tmp_path.name
    assert embedder.encode_queries([]) is None


def test_embedder_load_failure_disables_it(tmp_path, monkeypatch):
    import sentence_transformers

    def broken(*args, **kwargs):
        raise OSError("веса повреждены")

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", broken)
    embedder = E.Embedder(tmp_path, True)
    assert embedder.encode_queries(["x"]) is None
    assert not embedder.enabled  # больше не пытается грузить


def test_get_embedder_reads_config(tmp_path, monkeypatch):
    monkeypatch.setattr(E.config, "EMBEDDER_PATH", str(tmp_path / "m"))
    monkeypatch.setattr(E.config, "EMBEDDER_ENABLED", False)
    E.get_embedder.cache_clear()
    try:
        embedder = E.get_embedder()
        assert embedder.path == tmp_path / "m" and not embedder.enabled
        assert E.get_embedder() is embedder
    finally:
        E.get_embedder.cache_clear()


# --- офлайн-векторы --------------------------------------------------------------------------


def test_embed_save_load_roundtrip(tmp_path):
    kb = _mini_kb()
    embedder = FakeEmbedder({C.full_card(f): _DOC_VECTORS[f.id] for f in kb.families.values()})
    vectors = V.embed_families(kb, embedder)
    assert vectors.ids == list(kb.families)  # и конкретные семейства, и общие позиции
    assert vectors.matrix.shape == (6, 3) and vectors.model == "fake-model"
    V.save_vectors(vectors, tmp_path, seconds=1.25)
    meta = json.loads((tmp_path / V.VECTORS_META).read_text(encoding="utf-8"))
    assert meta["dim"] == 3 and meta["ids"] == vectors.ids and meta["seconds"] == 1.2
    loaded = V.load_vectors(tmp_path)
    assert loaded.ids == vectors.ids and np.allclose(loaded.matrix, vectors.matrix)
    assert loaded.card_sha256 == V.cards_sha256(V.embeddable(kb))


def test_card_hash_tracks_meta_changes():
    kb = _mini_kb()
    before = V.cards_sha256(V.embeddable(kb))
    kb.apply_meta({"F-GRIND": {"synonyms": ["болгарка"]}})
    assert V.cards_sha256(V.embeddable(kb)) != before


def test_load_vectors_rejects_missing_or_inconsistent(tmp_path):
    assert V.load_vectors(tmp_path) is None
    np.save(tmp_path / V.VECTORS_FILE, np.zeros((2, 3), dtype=np.float32))
    (tmp_path / V.VECTORS_META).write_text(json.dumps({"ids": ["a"], "dim": 3}), encoding="utf-8")
    assert V.load_vectors(tmp_path) is None
    (tmp_path / V.VECTORS_META).write_text(json.dumps({"ids": ["a", "b"], "dim": 4}))
    assert V.load_vectors(tmp_path) is None
    (tmp_path / V.VECTORS_META).write_text("{битый json")
    assert V.load_vectors(tmp_path) is None


def test_embed_main_fails_cleanly_without_model(tmp_path, capsys):
    from apps.ste.kb_build.build import save

    save(_mini_kb(), tmp_path)
    code = V.main(["--kb", str(tmp_path), "--model", str(tmp_path / "нет-модели")])
    assert code == 1 and "Эмбеддер недоступен" in capsys.readouterr().err
    assert not (tmp_path / V.VECTORS_FILE).exists()


def test_embed_run_writes_files(tmp_path):
    from apps.ste.kb_build.build import save

    kb = _mini_kb()
    save(kb, tmp_path)
    embedder = FakeEmbedder({C.full_card(f): _DOC_VECTORS[f.id] for f in kb.families.values()})
    vectors, seconds = V.run(tmp_path, tmp_path / "out", embedder)
    assert seconds >= 0 and (tmp_path / "out" / V.VECTORS_FILE).exists()
    assert V.load_vectors(tmp_path / "out").ids == vectors.ids


def test_load_retriever_variants(tmp_path):
    from apps.ste.kb_build.build import load, save

    kb = _mini_kb()
    save(kb, tmp_path)
    assert not R.load_retriever(kb, tmp_path, FakeEmbedder({})).has_vectors  # векторов нет

    embedder = FakeEmbedder({C.full_card(f): _DOC_VECTORS[f.id] for f in kb.families.values()})
    V.run(tmp_path, tmp_path, embedder)
    hybrid = R.load_retriever(load(tmp_path), tmp_path, embedder)
    assert hybrid.has_vectors and not hybrid.vectors_stale

    off = E.Embedder(tmp_path / "нет-модели", True)
    assert not R.load_retriever(kb, tmp_path, off).has_vectors

    changed = load(tmp_path)
    changed.apply_meta({"F-GRIND": {"synonyms": ["болгарка"]}})
    assert R.load_retriever(changed, tmp_path, embedder).vectors_stale


# --- качество на настоящей базе ---------------------------------------------------------------
#
# Замер (см. отчёт в заголовке ``retrieval.py``): BM25 без LLM-метаданных — recall@1 ≈ 0,86,
# @5 ≈ 0,96, @30 ≈ 0,98. Пороги ниже — с запасом на изменение метаданных и пересборку базы.

FLOORS_BM25 = {1: 0.78, 5: 0.88, 10: 0.90, 30: 0.92}
FLOORS_RRF = {1: 0.80, 5: 0.92, 10: 0.95, 30: 0.97}
FLOORS_VECTOR = {1: 0.55, 5: 0.80, 10: 0.85, 30: 0.92}


def _rank(ids: list[str], expected: tuple[str, ...]) -> int | None:
    return next((index for index, fid in enumerate(ids, start=1) if fid in expected), None)


def _recall(ranks: list[int | None]) -> dict[int, float]:
    return {
        k: sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks)
        for k in (1, 5, 10, 30)
    }


POSITIVE = [(text, expected) for text, expected in QUERIES if expected]


def test_query_set_is_valid(real_kb):
    assert len(POSITIVE) >= 40
    for _, expected in POSITIVE:
        for family_id in expected:
            assert real_kb.families[family_id].role == "family", family_id


@pytest.fixture(scope="module")
def lexical(real_kb):
    return R.Retriever(real_kb)


def test_bm25_recall(lexical):
    ranks = [_rank([h.family_id for h in lexical.search(text)], exp) for text, exp in POSITIVE]
    recall = _recall(ranks)
    for k, floor in FLOORS_BM25.items():
        assert recall[k] >= floor, (k, recall)


def test_bm25_latency(lexical):
    timings = []
    for text, _ in QUERIES:
        started = time.perf_counter()
        lexical.search(text, k=30)
        timings.append((time.perf_counter() - started) * 1000)
    assert statistics.median(timings) < 20


def _assert_strong_is_precise(retriever):
    """Быстрый путь не смеет ошибаться: каждый «strong» — верное семейство."""
    wrong = []
    for text, expected in QUERIES:
        hits = retriever.search(text, k=30)
        if retriever.confidence(hits)["strong"] and hits[0].family_id not in expected:
            wrong.append((text, hits[0].name))
    assert wrong == []


def test_bm25_strong_is_precise(lexical):
    _assert_strong_is_precise(lexical)


def test_absent_product_is_not_strong(lexical):
    hits = lexical.search("перфоратор SDS-plus 800 Вт")
    assert hits and hits[0].name == "Бур для перфоратора"  # похоже, но это не перфоратор
    assert lexical.confidence(hits)["strong"] is False


def test_llm_synonyms_fix_colloquial_queries():
    """«Болгарка» и «УШМ» лексике недоступны, пока нет синонимов из LLM-метаданных (§3.7)."""
    from apps.ste.kb_build.build import load

    if not (KB_DIR / "ktru_kb.json.gz").exists():
        pytest.skip("база знаний КТРУ не собрана")
    kb = load(KB_DIR)
    kb.apply_meta({GRINDER: {"synonyms": ["болгарка", "УШМ", "углошлифовальная машина"]}})
    retriever = R.Retriever(kb)
    for text in ("болгарка Makita GA5030 125мм 840 Вт", "УШМ 125 мм с регулировкой оборотов"):
        assert retriever.search(text)[0].family_id == GRINDER, text


@pytest.fixture(scope="module")
def hybrid(real_kb):
    vectors = V.load_vectors(KB_DIR)
    if vectors is None:
        pytest.skip("векторы семейств не посчитаны (manage.py embed_ste_kb)")
    embedder = E.get_embedder()
    if not embedder.available:
        pytest.skip("нет весов эмбеддера (STE_EMBEDDER_PATH)")
    retriever = R.Retriever(real_kb, embedder, vectors)
    if not retriever.has_vectors:
        pytest.skip("векторы посчитаны другой моделью")
    return retriever


def test_hybrid_recall(hybrid):
    rrf, vector = [], []
    allowed = ~hybrid._generic
    for text, expected in POSITIVE:
        rrf.append(_rank([hit.family_id for hit in hybrid.search(text)], expected))
        cosines = hybrid.cosines(R.clean_query(text, hybrid.brands))
        order = np.argsort(-np.where(allowed, np.nan_to_num(cosines, nan=-1.0), -2.0))
        vector.append(_rank([hybrid.families[i].id for i in order[:30]], expected))
    for k, floor in FLOORS_RRF.items():
        assert _recall(rrf)[k] >= floor, (k, _recall(rrf))
    for k, floor in FLOORS_VECTOR.items():
        assert _recall(vector)[k] >= floor, (k, _recall(vector))


def test_hybrid_strong_is_precise(hybrid):
    _assert_strong_is_precise(hybrid)
