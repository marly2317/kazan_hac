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
