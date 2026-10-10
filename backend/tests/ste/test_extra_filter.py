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
