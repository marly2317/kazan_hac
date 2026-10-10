"""Шаги LLM-1/LLM-2, промпты и метаданные семейств на реальной базе знаний.

Ответы модели — правдоподобные подделки через httpx.MockTransport (ограда ```json, отклонения
от схемы, выдуманные цитаты); к настоящему API тесты не обращаются.
"""

from __future__ import annotations

import io
import json

import httpx
import pytest
from django.core.management import CommandError, call_command

from apps.ste import config
from apps.ste.application import family_meta as M
from apps.ste.application import llm_steps as S
from apps.ste.application import prompts as P
from apps.ste.domain.model import Confidence, Source, Status
from apps.ste.infrastructure import llm_client as L
from apps.ste.infrastructure.llm_client import LlmClient
from apps.ste.kb_build.build import load

GRINDER = "28.24.11.000-00000042"  # Шлифмашина
SCREWDRIVER = "28.24.11.000-00000032"  # Шуруповерт
DESK = "31.01.10.000-00000006"  # Стол письменный
COLD = "14.12.30.160-00000340"  # Одежда специальная для защиты от пониженных температур
BOOTS = "15.20.11.113-00000015"  # Сапоги резиновые формовые
SOCKET = "25.73.30.176-00000002"  # Головка сменная торцевая


@pytest.fixture(scope="module")
def kb():
    return load(config.KB_DIR)


@pytest.fixture(autouse=True)
def _clean_state():
    L.reset_state()
    yield
    L.reset_state()


def completion(content: str, model: str = "gemma") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020},
        },
    )


class Fake:
    def __init__(self, *items) -> None:
        self.queue = list(items)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.queue:
            raise AssertionError("неожиданный HTTP-запрос")
        return self.queue.pop(0)

    def messages(self, index: int = 0) -> list[dict]:
        return json.loads(self.requests[index].content)["messages"]


def fenced(obj) -> str:
    """Как отвечает бесплатный провайдер: JSON в ограде, с пояснением вокруг."""
    return "Вот результат:\n```json\n" + json.dumps(obj, ensure_ascii=False, indent=1) + "\n```"


def llm(*answers, cache_dir=None, **overrides) -> tuple[LlmClient, Fake]:
    fake = Fake(
        *[item if isinstance(item, httpx.Response) else completion(item) for item in answers]
    )
    params = {
        "base_url": "https://router.test/api/v1",
        "api_key": "sk-test",
        "model": "gemma",
        "transport": httpx.MockTransport(fake),
        "cache_dir": cache_dir,
        "sleep": lambda _seconds: None,
    }
    params.update(overrides)
    return LlmClient(**params), fake


def no_llm(**overrides) -> LlmClient:
    def refuse(request):
        raise AssertionError("HTTP-запрос не ожидался")

    return LlmClient(
        "https://router.test/api/v1",
        "sk",
        "gemma",
        transport=httpx.MockTransport(refuse),
        **overrides,
    )


def cid(family, key: str) -> str:
    return {char_key: short for short, char_key in P.char_ids(family).items()}[key]


def item(family, key, value, quote, status="указано", unit=None) -> dict:
    return {"id": cid(family, key), "status": status, "value": value, "unit": unit, "quote": quote}


# === LLM-1: выбор семейства ===================================================================


def test_select_family_normalizes_ids_scores_and_order(kb):
    grinder, screwdriver, desk = (kb.families[code] for code in (GRINDER, SCREWDRIVER, DESK))
    generic = kb.generic()[:3]
    cards = [f"[{family.short_id}] {family.name}" for family in (grinder, screwdriver, desk)]
    generic_cards = [f"[{family.short_id}] {family.name}" for family in generic]
    allowed = [family.short_id for family in (grinder, screwdriver, desk, *generic)]
    short_generic = "F" + str(int(generic[1].short_id[1:]))  # «F8» вместо «F008»
    answer = fenced(
        {
            "candidates": [
                {"family_id": screwdriver.short_id, "score": 0.2, "reason": "электроинструмент"},
                {"family_id": grinder.short_id, "score": 95, "reason": "«болгарка» — это УШМ"},
                {"family_id": short_generic, "score": "0,1", "reason": "общая позиция"},
                {"family_id": "F999", "score": 0.6, "reason": "нет в списке"},
                {"family_id": desk.short_id, "score": 0.05, "reason": "четвёртый лишний"},
            ],
            "not_found": "false",
            "not_found_reason": "",
        }
    )
    client, fake = llm(answer)
    result = S.select_family(client, "Болгарка Makita 125 мм", cards, generic_cards, allowed)

    assert result.candidates == [
        (grinder.short_id, 0.95, "«болгарка» — это УШМ"),
        (screwdriver.short_id, 0.2, "электроинструмент"),
        (generic[1].short_id, 0.1, "общая позиция"),
    ]
    assert result.not_found is False and result.not_found_reason is None
    assert result.response.attempts == 1  # отклонения исправлены без повторного запроса

    system, user = fake.messages()
    assert system["role"] == "system" and user["role"] == "user"
    content = system["content"]
    # Длинный список — в начале (кэш префикса), правила — после списков.
    assert content.index(cards[0]) < content.index(generic_cards[0]) < content.index("Правила:")
    assert "ОБЩИЕ ПОЗИЦИИ" in content
    assert user["content"].startswith("Описание товара: «Болгарка Makita 125 мм»")
    # Короткий enum (6 id) перечисляется в схеме; длинный (полный каталог) — заменяется описанием.
    assert f'"family_id": "{grinder.short_id}" | "{screwdriver.short_id}"' in user["content"]
    long_ids = [family.short_id for family in kb.selectable()]
    client, fake = llm(answer)
    S.select_family(client, "Болгарка", cards, generic_cards, long_ids)
    long_user = fake.messages()[1]["content"]
    assert "id семейства из списка" in long_user and long_ids[-1] not in long_user


def test_select_family_not_found(kb):
    grinder = kb.families[GRINDER]
    answer = '{"candidates": [], "not_found": true, "not_found_reason": "Это продукты питания"}'
    client, _ = llm(answer)
    result = S.select_family(
        client, "Сыр российский 1 кг", ["[F399] Шлифмашина"], [], [grinder.short_id]
    )
    assert result.candidates == [] and result.not_found is True
    assert result.not_found_reason == "Это продукты питания"


def test_select_family_only_unknown_ids_means_not_found(kb):
    client, _ = llm('{"candidates": [{"family_id": "F777", "score": 0.9, "reason": "?"}]}')
    result = S.select_family(client, "что-то", ["[F399] Шлифмашина"], [], ["F399"])
    assert result.candidates == [] and result.not_found is True and result.not_found_reason


def test_select_family_json_schema_mode_puts_ids_into_enum():
    client, fake = llm(
        '{"candidates": [{"family_id": "F399", "score": 0.9, "reason": "болгарка"}],'
        ' "not_found": false, "not_found_reason": null}',
        structured_mode="json_schema",
    )
    S.select_family(client, "болгарка", ["[F399] Шлифмашина"], ["[F001] Прочее"], ["F399", "F001"])
    schema = json.loads(fake.requests[0].content)["response_format"]["json_schema"]["schema"]
    family_id = schema["properties"]["candidates"]["items"]["properties"]["family_id"]
    assert family_id["enum"] == ["F399", "F001"]


def test_select_family_skips_llm_for_empty_input():
    client = no_llm()
    assert S.select_family(client, "   ", ["[F399] x"], [], ["F399"]).not_found is True
    assert S.select_family(client, "болгарка", [], [], []).not_found is True


# === LLM-2: разложение по схеме ===============================================================

GRINDER_TEXT = (
    "Болгарка Makita GA5030 125мм 0,84 кВт от сети, с регулировкой оборотов, кейс в комплекте"
)


def test_extract_values_grinder_realistic(kb):
    family = kb.families[GRINDER]
    answer = fenced(
        {
            "characteristics": [
                item(family, "вид", "углошлифовальная машина", "Болгарка"),
                item(family, "диаметр диска", 125, "125мм", unit="мм"),
                item(family, "мощность", "0,84", "0,84 кВт"),
                item(family, "тип питания", "От сети", "от сети"),
                item(family, "емкость аккумулятора", 2, "аккумулятор 2 Ач", unit="Ач"),
                item(family, "скорость", None, None, status="не указано"),
            ],
            "extra": [
                {"name": "регулировка оборотов", "value": "есть", "unit": None,
                 "quote": "с регулировкой оборотов"},
                {"name": "Кейс", "value": "в комплекте", "unit": None, "quote": "кейс в комплекте"},
                {"name": "Гарантия", "value": "3 года", "unit": None, "quote": "гарантия 3 года"},
            ],
            "brand": "Makita",
            "model": "GA5030",
        }
    )  # fmt: skip
    client, fake = llm(answer)
    result = S.extract_values(client, GRINDER_TEXT, family)

    values = result.values
    assert list(values) == list(family.chars)  # все характеристики, в порядке семейства
    kind = values["вид"]
    assert kind.value == "Углошлифовальная машина" and kind.status == Status.SPECIFIED
    assert kind.quote_found and kind.confidence == Confidence.CONFIDENT
    assert kind.source == Source.TEXT
    disc = values["диаметр диска"]
    assert (disc.value, disc.unit, disc.confidence) == (125.0, "мм", Confidence.CONFIDENT)
    power = values["мощность"]
    assert power.value == pytest.approx(0.84) and power.unit == "кВт"  # единица — из цитаты
    assert values["тип питания"].value == "От сети"
    battery = values["емкость аккумулятора"]  # выдуманная цитата
    assert battery.quote_found is False and battery.confidence == Confidence.WEAK
    absent = [key for key, value in values.items() if value.status == Status.ABSENT]
    assert "скорость" in absent and len(absent) == len(family.chars) - 5
    assert all(values[key].value is None for key in absent)

    assert result.extra == [
        {"name": "Регулировка оборотов", "value": "есть", "unit": None,
         "quote": "с регулировкой оборотов"},
        {"name": "Кейс", "value": "в комплекте", "unit": None, "quote": "кейс в комплекте"},
    ]  # fmt: skip
    assert (result.brand, result.model) == ("Makita", "GA5030")
    assert result.duplicates == {}

    system, user = fake.messages()
    assert "Семейство: Шлифмашина" in system["content"]
    assert f"{cid(family, 'вид')} — Вид — текст — — обязательная — допустимые:" in system["content"]
    assert (
        f"{cid(family, 'мощность')} — Мощность — число — Ватт — необязательная" in system["content"]
    )
    assert "Количество оборотов в минуту — число — — обязательная" in system["content"]
    assert user["content"].startswith(f"Описание товара: «{GRINDER_TEXT}»")
    assert user["content"].rstrip().endswith(L.JSON_ONLY)


def test_extract_values_desk_contradiction_multi_and_section_3_6_format(kb):
    family = kb.families[DESK]
    text = "Стол письменный угловой, столешница из пластика, 2 ящика, полка для клавиатуры и замок"
    answer = json.dumps(
        {
            "characteristics": {  # формат §3.6: объект по id, часть полей пропущена
                cid(family, "конфигурация стола"): {"status": "указано", "value": "Угловой",
                                                    "quote": "угловой"},
                cid(family, "вид материала столешницы"): {"status": "противоречие",
                                                          "value": "пластик",
                                                          "quote": "столешница из пластика"},
                cid(family, "количество выдвижных ящиков"): {"status": "указано", "value": "2",
                                                             "unit": None, "quote": "2 ящика"},
                cid(family, "дополнительное оборудование"): {
                    "status": "указано",
                    "value": ["полка для клавиатуры", "Замок в выдвижном ящике"],
                    "quote": "полка для клавиатуры и замок",
                },
                cid(family, "тип каркаса"): {"status": "не указано", "value": None, "quote": None},
            },
            "extra": [],
            "brand": None,
            "model": None,
        },
        ensure_ascii=False,
    )  # fmt: skip
    client, _ = llm(answer)
    result = S.extract_values(client, text, family)
    values = result.values

    assert values["конфигурация стола"].value == "Угловой"
    top = values["вид материала столешницы"]
    # Недопустимое значение не подменено ближайшим — его покажет модуль ошибок.
    assert (top.status, top.value, top.confidence) == (
        Status.CONTRADICTION,
        "пластик",
        Confidence.WEAK,
    )
    assert top.quote_found is True
    drawers = values["количество выдвижных ящиков"]
    assert (drawers.value, drawers.unit) == (2.0, None)  # «ящика» — не единица
    extras = values["дополнительное оборудование"]
    assert extras.value == ["Полка для клавиатуры", "Замок в выдвижном ящике"]
    assert extras.confidence == Confidence.CONFIDENT
    assert values["тип каркаса"].status == Status.ABSENT
    assert result.brand is None and result.model is None


def test_extract_values_duplicates_and_multi_merge(kb):
    grinder = kb.families[GRINDER]
    text = "Шлифмашина угловая, мощность 800 Вт, мощность 1200 Вт, болгарка"
    answer = json.dumps(
        {
            "characteristics": [
                item(grinder, "мощность", 800, "мощность 800 Вт", unit="Вт"),
                item(grinder, "вид", "Углошлифовальная машина", "Шлифмашина угловая"),
                item(grinder, "мощность", 1200, "мощность 1200 Вт", unit="Вт"),
                item(grinder, "вид", "углошлифовальная машина", "болгарка"),
            ],
            "extra": [],
            "brand": None,
            "model": None,
        },
        ensure_ascii=False,
    )
    result = S.extract_values(llm(answer)[0], text, grinder)
    assert result.values["мощность"].value == 800.0
    assert [dup.value for dup in result.duplicates["мощность"]] == [1200.0]
    assert "вид" not in result.duplicates  # то же значение дважды — не дубль

    desk = kb.families[DESK]
    text = "Стол письменный: полка для клавиатуры, замок в выдвижном ящике"
    answer = json.dumps(
        {
            "characteristics": [
                item(desk, "дополнительное оборудование", ["Полка для клавиатуры"],
                     "полка для клавиатуры"),
                item(desk, "дополнительное оборудование", "Замок в выдвижном ящике",
                     "замок в выдвижном ящике"),
            ],
            "extra": [],
            "brand": None,
            "model": None,
        },
        ensure_ascii=False,
    )  # fmt: skip
    merged = S.extract_values(llm(answer)[0], text, desk).values["дополнительное оборудование"]
    assert merged.value == ["Полка для клавиатуры", "Замок в выдвижном ящике"]
    assert merged.quote == "полка для клавиатуры … замок в выдвижном ящике"
    assert merged.quote_found and merged.confidence == Confidence.CONFIDENT


def test_extract_values_tolerates_sloppy_ids_statuses_and_types(kb):
    family = kb.families[GRINDER]
    text = "Аккумуляторная болгарка макита, диаметр 125 мм, пылесос подключается"
    answer = fenced(
        {
            "characteristics": [
                {"id": "c1", "status": "Указано.", "value": "Углошлифовальная машина",
                 "quote": "болгарка"},
                {"id": "Диаметр диска", "status": "specified", "value": "125мм", "unit": None,
                 "quote": "125мм"},
                {"id": cid(family, "возможность подключить пылесос").upper(), "status": None,
                 "value": True, "quote": "пылесос подключается"},
                {"id": "c99", "status": "указано", "value": "x", "quote": "x"},
                "мусор",
            ],
            "extra": [
                {"name": "Тип питания", "value": "От аккумулятора", "unit": None,
                 "quote": "Аккумуляторная"},
                {"name": "", "value": "без имени"},
            ],
            "brand": "Makita",
            "model": "null",
        }
    )  # fmt: skip
    client, fake = llm(answer)
    result = S.extract_values(client, text, family)
    values = result.values
    assert len(fake.requests) == 1  # всё исправлено без запроса на исправление
    assert values["вид"].value == "Углошлифовальная машина"
    disc = values["диаметр диска"]
    assert (disc.value, disc.unit) == (125.0, "мм") and disc.quote_found  # «125мм» ~ «125 мм»
    assert values["возможность подключить пылесос"].value == "Да"
    # Характеристика из таблицы, положенная моделью в extra, вернулась в характеристики.
    power = values["тип питания"]
    assert power.value == "От аккумулятора" and power.confidence == Confidence.CONFIDENT
    assert result.extra == []
    assert result.brand == "Makita"  # в тексте «макита» — упоминание засчитано
    assert result.model is None


def test_extract_values_drops_unmentioned_brand(kb):
    family = kb.families[GRINDER]
    answer = '{"characteristics": [], "extra": [], "brand": "Bosch", "model": "GWS 750"}'
    result = S.extract_values(llm(answer)[0], "болгарка 125 мм", family)
    assert result.brand is None and result.model is None
    assert all(value.status == Status.ABSENT for value in result.values.values())


def test_extract_values_repair_then_cache(kb, tmp_path):
    family = kb.families[GRINDER]
    good = json.dumps(
        {"characteristics": [item(family, "вид", "Углошлифовальная машина", "болгарка")],
         "extra": [], "brand": None, "model": None},
        ensure_ascii=False,
    )  # fmt: skip
    client, fake = llm("Конечно! Болгарка — это УШМ.", good, cache_dir=tmp_path)
    first = S.extract_values(client, "болгарка", family)
    assert first.response.attempts == 2 and len(fake.requests) == 2
    again = S.extract_values(no_llm(cache_dir=tmp_path), "болгарка", family)
    assert again.response.cached is True
    assert again.values["вид"].value == first.values["вид"].value == "Углошлифовальная машина"


def test_extract_values_numeric_sizes_and_lookalike_letters(kb):
    boots = kb.families[BOOTS]
    answer = json.dumps(
        {"characteristics": [item(boots, "размер (штихмассовый)", [44.5, "45"], "44,5 и 45")],
         "extra": [], "brand": None, "model": None}
    )  # fmt: skip
    sizes = S.extract_values(llm(answer)[0], "Сапоги резиновые, размеры 44,5 и 45", boots)
    assert sizes.values["размер (штихмассовый)"].value == ["44,5", "45"]

    socket = kb.families[SOCKET]
    torx = socket.chars["размер головки torx"].text_values()
    latin = json.dumps(
        {"characteristics": [item(socket, "размер головки torx", "E10", "E10")],
         "extra": [], "brand": None, "model": None}
    )  # fmt: skip
    result = S.extract_values(llm(latin)[0], "Головка торцевая TORX E10", socket)
    value = result.values["размер головки torx"].value
    assert value in torx and value == "Е10"  # в КТРУ «Е» кириллическая


def test_extract_values_without_text_or_chars_makes_no_call(kb):
    client = no_llm()
    grinder = kb.families[GRINDER]
    result = S.extract_values(client, "  ", grinder)
    assert all(value.status == Status.ABSENT for value in result.values.values())
    generic = kb.generic()[0]
    assert S.extract_values(client, "мебель прочая", generic).values == {}


# === мини-LLM-2 ==============================================================================


def test_extract_one_button_free_text(kb):
    family = kb.families[SCREWDRIVER]
    allowed = family.chars["тип питания"].text_values()
    battery = next(value for value in allowed if "аккум" in value.casefold())
    answer = json.dumps(
        {"status": "указано", "value": battery.lower(), "unit": None, "quote": "аккумуляторный"},
        ensure_ascii=False,
    )
    client, fake = llm(answer)
    value = S.extract_one(client, "аккумуляторный, 18 вольт", family, "тип питания")
    assert value.value == battery and value.source == Source.ANSWER
    assert value.status == Status.SPECIFIED and value.confidence == Confidence.CONFIDENT
    system, user = fake.messages()
    assert "Тип питания — текст — — обязательная — допустимые:" in system["content"]
    assert "Ответ пользователя: «аккумуляторный, 18 вольт»" in user["content"]


def test_extract_one_numeric_and_wrapped_answer(kb):
    family = kb.families[GRINDER]
    answer = fenced(
        {"characteristics": [{"id": "c01", "status": "указано", "value": 125, "unit": "мм",
                              "quote": "125 мм"}]}
    )  # fmt: skip
    value = S.extract_one(llm(answer)[0], "125 мм", family, "диаметр диска")
    assert (value.value, value.unit, value.quote_found) == (125.0, "мм", True)


def test_extract_one_unknown_answer_returns_none(kb):
    family = kb.families[GRINDER]
    answer = '{"status": "не указано", "value": null, "unit": null, "quote": null}'
    assert S.extract_one(llm(answer)[0], "не знаю", family, "тип питания") is None
    assert S.extract_one(no_llm(), "", family, "тип питания") is None
    with pytest.raises(KeyError):
        S.extract_one(no_llm(), "что-то", family, "нет такой")


# === разбор значений и цитат ==================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (840, (840.0, None)),
        (0.84, (0.84, None)),
        ("0,84", (0.84, None)),
        ("1 200", (1200.0, None)),
        ("1\u00a0200,5", (1200.5, None)),
        ("1\u202f200", (1200.0, None)),
        ("125мм", (125.0, "мм")),
        ("125 мм.", (125.0, "мм")),
        ("≈800", (800.0, None)),
        ("около 5 кг", (5.0, "кг")),
        ("800-900", (800.0, None)),
        ([3, 4], (3.0, None)),
        ("много", (None, None)),
        (True, (None, None)),
        (None, (None, None)),
        (float("nan"), (None, None)),
    ],
)
def test_parse_llm_number(raw, expected):
    assert S.parse_llm_number(raw) == expected


@pytest.mark.parametrize(
    ("quote", "number", "unit"),
    [
        ("0,84 кВт", 0.84, "кВт"),
        ("скорость 1200 об/мин", 1200, "об/мин"),
        ("2 ящика", 2, None),
        ("125 мм и 150 мм", 150, "мм"),
        (None, 5, None),
    ],
)
def test_unit_from_quote(quote, number, unit):
    assert S.unit_from_quote(quote, number) == unit


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("углошлифовальная машина", "Углошлифовальная машина"),
        ("«Углошлифовальная машина».", "Углошлифовальная машина"),
        ("Углошлифовальная машинка", "Углошлифовальная машина"),
        ("углошлифовальная", "Углошлифовальная машина"),
        ("Эксцентриковая", "Эксцентриковая (орбитальная)"),
        ("от сети 220 В", "От сети"),
        ("пластик", "пластик"),
    ],
)
def test_coerce_text_value_grinder(kb, value, expected):
    chars = kb.families[GRINDER].chars
    allowed = chars["вид"].text_values() + chars["тип питания"].text_values()
    assert S.coerce_text_value(value, allowed) == expected


def test_coerce_text_value_edge_cases(kb):
    cold = kb.families[COLD]
    kinds = cold.chars["вид одежды"].text_values()
    assert S.coerce_text_value("куртка утеплённая", kinds) == "Куртка"
    frame = kb.families[DESK].chars["тип каркаса"].text_values()
    assert S.coerce_text_value("не металлический", frame) == "не металлический"
    assert S.coerce_text_value("металлический", frame) == "Металлический"
    assert S.coerce_text_value("Мужской", ["Мужской", "Женский"]) == "Мужской"
    assert S.coerce_text_value("унисекс", ["Мужской", "Женский"]) == "унисекс"
    assert S.coerce_text_value("что угодно", []) == "что угодно"
    assert S.coerce_text_value("44", ["44,5", "45"]) == "44"  # не часть «44,5»


@pytest.mark.parametrize(
    ("quote", "found"),
    [
        ("Болгарка", True),
        ("болгарка makita", True),
        ("УТЕПЛЁННАЯ куртка", True),
        ("«125мм»", True),  # нечётко: в тексте «125 мм»
        ("от  сети", True),
        ("болгарка … 840 вт", True),
        ("болгарка ... 1500 вт", False),
        ("1200 Вт", False),
        ("XL", True),
        ("XXL", False),
        ("", False),
        (None, False),
    ],
)
def test_verify_quote(quote, found):
    text = "Болгарка Makita утепленная куртка 125 мм 840 Вт от сети, размер XL"
    assert S.verify_quote(quote, text) is found


def test_is_mentioned_handles_transliteration_and_spacing():
    assert S.is_mentioned("Makita", "болгарка макита 125")
    assert S.is_mentioned("GA5030", "болгарка ga 5030")
    assert S.is_mentioned("Bosch", "шуруповерт бош")
    assert not S.is_mentioned("DeWalt", "болгарка 125 мм")
    assert not S.is_mentioned(None, "что угодно")


# === промпты ==================================================================================


def test_char_ids_follow_family_order(kb):
    family = kb.families[GRINDER]
    ids = P.char_ids(family)
    assert list(ids) == [f"c{index:02d}" for index in range(1, len(family.chars) + 1)]
    assert list(ids.values()) == list(family.chars)


def test_long_enum_relevance_filter(kb):
    boots = next(family for family in kb.selectable() if family.name.startswith("Ботики"))
    sizes = boots.chars["размер (штихмассовый)"].text_values()
    assert len(sizes) > P.ENUM_LIMIT
    shown, truncated = P.relevant_values(sizes, "Ботики резиновые, размер 23,5")
    assert truncated and len(shown) <= P.ENUM_LIMIT
    assert shown[0] == "23,5" and "23" not in shown[:1]
    assert sizes[0] in shown  # голова списка — для ориентира модели
    assert P.relevant_values(sizes[:10], "что угодно") == (sizes[:10], False)

    line = P.char_line("c03", boots.chars["размер (штихмассовый)"], "ботики 23,5")
    assert f"показано {P.ENUM_LIMIT} из {len(sizes)}" in line and "23,5" in line

    socket = kb.families[SOCKET]
    inches = socket.chars["размер, дюйм"].text_values()
    shown, _ = P.relevant_values(inches, "головка торцевая 2 15/16 дюйма")
    assert "2 15/16" in shown


def test_family_meta_prompt(kb):
    grinder, cold = kb.families[GRINDER], kb.families[COLD]
    system, user = P.family_meta_messages([grinder, cold])
    assert "без брендов" in system["content"]
    assert f"[{grinder.short_id}] Шлифмашина" in user["content"]
    assert "виды: Радиальношлифовальная ручная машина" in user["content"]
    assert f"({grinder.short_id}, {cold.short_id})" in user["content"]
    described = next(family for family in kb.selectable() if len(family.description or "") > 300)
    block = P.family_meta_block(described)
    line = next(row for row in block.splitlines() if row.strip().startswith("описание:"))
    assert len(line.split(": ", 1)[1]) == P.META_DESCRIPTION_LIMIT and line.endswith("…")


# === метаданные семейств (§3.7) ===============================================================


def _meta_answer(family, **extra) -> dict:
    return {
        family.short_id: {
            "synonyms": [f"синоним {family.name.lower()}", "ещё синоним"],
            "description": f"Описание {family.name}.",
            "keywords": ["ключ"],
        },
        **extra,
    }


def test_generate_meta_cleans_resumes_and_saves(kb, tmp_path):
    grinder, screwdriver, desk = (kb.families[code] for code in (GRINDER, SCREWDRIVER, DESK))
    batch1 = {
        grinder.short_id: {
            "synonyms": ["Болгарка", "УШМ", "болгарка", "углошлифовалка", "Makita", "Шлифмашина",
                         "GA5030", "шлифовальная машинка", "  "],
            "description": "Ручной электроинструмент для шлифования и резки.",
            "keywords": ["Шлифование", "диск", "Bosch"],
        },
        "F9999": {"synonyms": ["лишнее"], "description": "x", "keywords": []},
    }  # fmt: skip
    batch2 = {"families": _meta_answer(desk)}  # обёртка — тоже принимается
    client, fake = llm(fenced(batch1), json.dumps(batch2, ensure_ascii=False))
    path = tmp_path / M.META_FILE
    saved: list[int] = []

    def on_batch(meta):
        saved.append(len(meta))
        M.save_meta(path, meta)

    run = M.generate_meta(client, [grinder, screwdriver, desk], {}, batch_size=2, on_batch=on_batch)

    assert len(fake.requests) == 2 and run.requests == 2 and run.stopped is None
    assert run.done == [grinder.id, desk.id] and run.missing == [screwdriver.id]
    entry = run.meta[grinder.id]
    assert entry["synonyms"] == ["болгарка", "ушм", "углошлифовалка", "шлифовальная машинка"]
    assert entry["keywords"] == ["шлифование", "диск"]
    assert entry["description"] == "Ручной электроинструмент для шлифования и резки."
    assert (entry["short_id"], entry["name"]) == (grinder.short_id, "Шлифмашина")
    assert run.meta[desk.id]["synonyms"] == [f"синоним {desk.name.lower()}", "ещё синоним"]
    assert saved == [1, 2]
    assert M.load_meta(path) == run.meta
    assert grinder.short_id in fake.messages(0)[1]["content"]
    assert desk.short_id not in fake.messages(0)[1]["content"]

    # Продолжение: готовые пропускаются — один запрос только за шуруповертом.
    client, fake = llm(fenced(_meta_answer(screwdriver)))
    again = M.generate_meta(client, [grinder, screwdriver, desk], M.load_meta(path), batch_size=2)
    assert len(fake.requests) == 1 and again.done == [screwdriver.id]
    assert set(again.meta) == {grinder.id, screwdriver.id, desk.id}
    user = fake.messages(0)[1]["content"]
    assert screwdriver.short_id in user and grinder.short_id not in user

    # Сгенерированный файл подмешивается в базу знаний.
    M.save_meta(path, again.meta)
    fresh = load(config.KB_DIR)
    fresh.apply_meta(M.load_meta(path))
    assert fresh.families[GRINDER].synonyms[:2] == ["болгарка", "ушм"]
    assert fresh.families[GRINDER].summary.startswith("Ручной электроинструмент")


def test_generate_meta_limits_batches_and_survives_failures(kb):
    families = [kb.families[code] for code in (GRINDER, SCREWDRIVER, DESK)]
    client, fake = llm(fenced(_meta_answer(families[0])), fenced(_meta_answer(families[1])))
    run = M.generate_meta(client, families, {}, batch_size=1, max_batches=2)
    assert len(fake.requests) == 2 and len(run.done) == 2

    # Пакет с мусором дважды — пропущен, следующий пакет всё равно делается.
    client, fake = llm("не JSON", "опять не JSON", fenced(_meta_answer(families[1])))
    run = M.generate_meta(client, families[:2], {}, batch_size=1)
    assert run.missing == [families[0].id] and run.done == [families[1].id]

    # Дневной лимит — остановка без исключения, сделанное не теряется.
    quota = httpx.Response(429, json={"error": {"message": "free-models-per-day", "code": 429}})
    client, fake = llm(fenced(_meta_answer(families[0])), quota)
    run = M.generate_meta(client, families, {}, batch_size=1)
    assert run.done == [families[0].id] and "Дневной лимит" in run.stopped
    assert len(fake.requests) == 2


def test_clean_entry_rejects_empty_and_caps(kb):
    grinder = kb.families[GRINDER]
    assert M.clean_entry(None, grinder) is None
    assert M.clean_entry({"synonyms": [], "description": ""}, grinder) is None
    many = {"synonyms": [f"слово {chr(0x430 + i)}" for i in range(30)], "description": "д" * 500,
            "keywords": "диск; резка, шлифование"}  # fmt: skip
    entry = M.clean_entry(many, grinder, M.load_brands(None))
    assert len(entry["synonyms"]) == M.MAX_SYNONYMS
    assert len(entry["description"]) == M.MAX_DESCRIPTION
    assert entry["keywords"] == ["диск", "резка", "шлифование"]


def test_load_brands_from_file(tmp_path):
    json_file = tmp_path / "brands.json"
    json_file.write_text('["Зубр-Мастер", "Caiman"]', encoding="utf-8")
    brands = M.load_brands(json_file)
    assert "caiman" in brands and "makita" in brands
    text_file = tmp_path / "brands.txt"
    text_file.write_text("Ставр\nPatriot\n", encoding="utf-8")
    assert "ставр" in M.load_brands(text_file)


# === команда gen_family_meta =================================================================


def test_command_dry_run_prints_batches_without_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "get_llm_client", lambda **_: pytest.fail("LLM не нужна в dry-run"))
    out = io.StringIO()
    call_command(
        "gen_family_meta",
        "--dry-run",
        "--batches",
        "2",
        "--out",
        str(tmp_path / "m.json"),
        stdout=out,
    )
    text = out.getvalue()
    assert "пакет 1:" in text and "пакет 2:" in text and "пакет 3:" not in text
    assert "к генерации: 288" in text or "к генерации:" in text


def test_command_requires_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://router.test/api/v1")
    with pytest.raises(CommandError, match="STE_LLM_API_KEY"):
        call_command("gen_family_meta", "--only", "F399", "--out", str(tmp_path / "m.json"))


def test_command_generates_with_mocked_client(kb, tmp_path, monkeypatch):
    grinder, desk = kb.families[GRINDER], kb.families[DESK]
    client, fake = llm(fenced({**_meta_answer(grinder), **_meta_answer(desk)}))
    monkeypatch.setattr(L, "get_llm_client", lambda **_: client)
    out_file = tmp_path / "m.json"
    out = io.StringIO()
    call_command(
        "gen_family_meta",
        "--only",
        f"{grinder.short_id},{desk.id}",
        "--out",
        str(out_file),
        stdout=out,
    )
    meta = json.loads(out_file.read_text(encoding="utf-8"))
    assert set(meta) == {grinder.id, desk.id} and len(fake.requests) == 1
    assert "Готово: 2 семейств" in out.getvalue()
    # Повторный запуск — нечего делать, запросов нет.
    monkeypatch.setattr(L, "get_llm_client", lambda **_: pytest.fail("не должно понадобиться"))
    out = io.StringIO()
    call_command("gen_family_meta", "--only", grinder.short_id, "--out", str(out_file), stdout=out)
    assert "Всё уже сгенерировано" in out.getvalue()
