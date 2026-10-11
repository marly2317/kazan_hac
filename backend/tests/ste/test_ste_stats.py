from apps.ste.kb_build.ste_stats import _tidy, split_characteristics

NAMES = sorted(["Тип", "Количество", "Цвет", "Тип застежки", "Вес"], key=len, reverse=True)


def test_split_extends_dictionary_name_up_to_the_value():
    raw = (
        "Тип сумки Сумка для обуви | Количество внешних карманов 3.00000 шт | "
        "Цвет светло-розовый, коричневый | Тип застежки Молния | Неизвестное поле 5"
    )
    assert split_characteristics(raw, NAMES) == [
        ("Тип сумки", "Сумка для обуви"),
        ("Количество внешних карманов", "3.00000 шт"),
        ("Цвет", "светло-розовый, коричневый"),
        ("Тип застежки", "Молния"),
    ]


def test_tidy_drops_export_zeros():
    assert _tidy("3.00000 шт") == "3 шт"
    assert _tidy("245 гр/м2") == "245 гр/м2"


def test_compare_card_with_real_ste():
    from apps.ste.domain.ste_compare import compare_with_ste

    typical = [
        {"name": "Напряжение аккумулятора", "share": 0.78, "values": ["18 В", "12 В"]},
        {"name": "Количество скоростей", "share": 0.95, "values": ["2"]},
        {"name": "Цвет корпуса", "share": 0.4, "values": ["зеленый"]},
    ]
    extra = [
        {"name": "Напряжение аккумулятора", "value": 18, "unit": "В"},
        {"name": "Подсветка", "value": "есть"},
    ]
    result = compare_with_ste(typical, 6967, {"color": "Цвет корпуса"}, extra)
    statuses = {row["name"]: row["status"] for row in result["typical"]}
    assert statuses == {
        "Напряжение аккумулятора": "filled",
        "Количество скоростей": "suggested",
        "Цвет корпуса": "ktru",
    }
    assert result["typical"][0]["value"] == "18 В"
    assert {item["name"]: item["status"] for item in result["ours"]} == {
        "Напряжение аккумулятора": "typical",
        "Подсветка": "rare",
    }
    assert result["summary"] == {"filled": 1, "suggested": 1, "rare": 1}
