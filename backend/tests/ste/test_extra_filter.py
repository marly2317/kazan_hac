from apps.ste.domain.extra_filter import clean_value, filter_extra


def test_drops_evaluation_and_general_fields():
    items = [
        {"name": "Принт", "value": "пони"},
        {"name": "Описание", "value": "афигенный"},
        {"name": "Качество", "value": "отличное"},
        {"name": "Внешность", "value": "очень классный"},
    ]
    assert filter_extra(items) == [{"name": "Принт", "value": "пони"}]


def test_cuts_evaluation_out_of_a_real_value():
    assert clean_value("красивый принт пони") == "принт пони"
    assert clean_value("синий") == "синий"
    assert clean_value("супер крутой") is None


def test_keeps_properties_that_look_like_evaluations():
    items = [
        {"name": "Класс защиты", "value": "2"},
        {"name": "Крутящий момент", "value": "50", "unit": "Н·м"},
        {"name": "Толщина", "value": "5 миллиметров"},
        {"name": "Комплектация", "value": ["кейс", "лучшее зарядное"]},
    ]
    kept = filter_extra(items)
    assert [item["name"] for item in kept] == [
        "Класс защиты",
        "Крутящий момент",
        "Толщина",
        "Комплектация",
    ]
    assert kept[3]["value"] == ["кейс", "зарядное"]


from apps.ste.domain.extra_filter import dedupe_extra, same_name  # noqa: E402

CHARS = {"tip": "Тип наконечника", "len": "Длина гвоздодера", "color": "Цвет изделия"}


def test_brand_in_extra_is_a_duplicate():
    items = [{"name": "Бренд", "value": "RAGE"}, {"name": "Масса", "value": "560", "unit": "г"}]
    keep, move, dropped = dedupe_extra(items, CHARS, {}, "RAGE", None)
    assert [item["name"] for item in keep] == ["Масса"]
    assert len(dropped) == 1 and not move


def test_same_char_under_another_name():
    filled = {"color": ("синий", "синий")}
    items = [
        {"name": "Цвет", "value": "синий", "quote": "синий"},  # уже есть — дубль
        {"name": "Длина", "value": 300, "unit": "мм", "quote": "300 мм"},  # основная пуста
    ]
    keep, move, dropped = dedupe_extra(items, CHARS, filled, None, None)
    assert keep == [] and [key for key, _ in move] == ["len"] and len(dropped) == 1


def test_wider_quote_is_not_a_duplicate():
    """«Молоток - гвоздодер 560 г» содержит «гвоздодер» основной, но масса — новая информация."""
    filled = {"tip": ("Гвоздодер", "гвоздодер")}
    items = [{"name": "Масса", "value": "560", "unit": "г", "quote": "Молоток - гвоздодер 560 г"}]
    keep, _, _ = dedupe_extra(items, CHARS, filled, None, None)
    assert keep == items


def test_generic_one_word_names_do_not_stick():
    assert same_name("Цвет", "Цвет изделия")
    assert same_name("Размер", "Размер одежды")
    assert not same_name("Тип", "Тип наконечника")
    assert not same_name("Материал подкладки", "Материал верха")
