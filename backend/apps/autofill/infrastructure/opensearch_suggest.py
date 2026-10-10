"""Подсказки по мере набора — конечным автоматом движка вместо списка в памяти.

Что подсказывается и зачем — в `apps.autofill.domain.suggest`: не произвольные русские слова,
а словарь КТРУ, потому что главная беда пользователя — написать слово, которого в справочнике
нет вовсе.

## Чем это лучше списка в памяти, и чем хуже

Хуже — временем. Префиксный поиск по отсортированному списку с `bisect` занимает микросекунду,
а здесь к такому же поиску добавляется сетевое обращение: 3–5 мс против 1 мкс. Для подсказки
при наборе разница незаметна (всё равно впереди HTTP до браузера), но честнее сказать прямо:
по скорости это проигрыш, и замер в `docs/SEARCH_CHOICE.md` его показывал.

Лучше — памятью и согласованностью. Индекс в памяти принадлежит **процессу**, и gunicorn с
тремя воркерами держит три копии словаря; он же строится обходом всего справочника при первом
запросе, то есть первый пользователь после перезапуска платит за всех. С движком словарь один
на стенд, строится один раз командой индексации, и после импорта справочника не нужно помнить
про сброс кэшей в трёх процессах.

На словаре из 2638 фраз это обмен не в нашу пользу, и прежняя реализация остаётся рядом —
`suggest_index.py`. Выбор делается в композиционном корне.

## Что осталось нашим

**Порядок показа.** Его считает `apps.autofill.domain.suggest.rank`: сначала слова, называющие
товар, затем вес источника, частота, длина. Это правило про предметную область — «пользователь
набирает название товара, а не хвост чужого наименования», — и у движка нет способа его знать.
Вес в автомате дополнения повторяет тот же порядок, но нужен он для другого: решить, что
попадёт в выборку, а что останется за её границей.
"""

from __future__ import annotations

import logging

from apps.autofill.domain.matching import stem
from apps.autofill.domain.suggest import Suggestion, SuggestionKind
from apps.ktru.infrastructure.search.client import client
from apps.ktru.infrastructure.search.indexes import TERMS

logger = logging.getLogger(__name__)

#: Во сколько раз больше слов запросить, чем показать.
#:
#: Ранжирует домен, а не движок, поэтому выбирать нужно с запасом: обрезка по весу автомата и
#: обрезка по `rank` — не одно и то же, и совпадают они только в начале списка. Двадцатикратный
#: запас покрывает самый населённый префикс словаря («по» — 146 слов) целиком.
OVERSELECT = 20


class OpenSearchSuggestionIndex:
    """Словарь справочника в движке: автомат дополнения плюс поиск по предыдущему слову."""

    def __init__(self, index: str | None = None) -> None:
        self._index = index

    @property
    def index(self) -> str:
        return self._index or TERMS.name

    def starting_with(self, prefix: str, limit: int) -> list[Suggestion]:
        """Слова справочника, начинающиеся на ``prefix`` — `completion suggester`.

        `skip_duplicates`: слово лежит в индексе по документу на каждое вхождение во фразу, и
        без этого на одном «металлический» выборка бы и закончилась. Остаётся документ с
        наибольшим весом, то есть с самым весомым источником, — он же несёт и ту фразу,
        которую стоит показать рядом со словом.
        """
        folded = prefix.casefold()
        if not folded:
            return []

        connection = client()
        if connection is None:
            return []

        body = {
            "_source": ["word", "kind", "source", "is_head", "frequency"],
            "suggest": {
                "words": {
                    "prefix": folded,
                    "completion": {
                        "field": "suggest",
                        "size": limit * OVERSELECT,
                        "skip_duplicates": True,
                    },
                }
            },
        }
        try:
            response = connection.search(index=self.index, body=body)
        except Exception as error:  # noqa: BLE001
            logger.warning("Подсказки недоступны (%s)", error)
            return []

        options = _options(response, "words")
        return [_suggestion(option["_source"]) for option in options]

    def following(self, word: str, limit: int) -> list[Suggestion]:
        """Слова, стоящие в справочнике сразу после ``word``.

        Это и есть «подскажи следующее слово»: после «куртка» справочник говорит «зимняя»,
        «мужская», «из» — не потому, что так устроен русский язык, а потому, что так устроен
        КТРУ.

        Отвечает агрегация, а не выборка документов: одно слово идёт после «куртка» в десяти
        наименованиях, и десять одинаковых подсказок вытеснили бы остальные. `terms` по слову
        с `top_hits` внутри отдаёт каждое слово один раз и вместе с его источником.
        """
        connection = client()
        if connection is None:
            return []

        body = {
            "size": 0,
            "query": {"term": {"previous": stem(word.casefold())}},
            "aggs": {
                "words": {
                    "terms": {
                        "field": "word.raw",
                        "size": limit * OVERSELECT,
                        # По частоте, а не по числу документов: частота считана по всему
                        # справочнику при индексации и говорит, насколько слово вообще
                        # употребимо, а число документов — лишь сколько раз оно встретилось
                        # после этого конкретного слова.
                        "order": {"weight": "desc"},
                    },
                    "aggs": {
                        "weight": {"max": {"field": "frequency"}},
                        "best": {
                            "top_hits": {
                                "size": 1,
                                "sort": [{"frequency": "desc"}],
                                "_source": [
                                    "word",
                                    "kind",
                                    "source",
                                    "is_head",
                                    "frequency",
                                ],
                            }
                        },
                    },
                }
            },
        }
        try:
            response = connection.search(index=self.index, body=body)
        except Exception as error:  # noqa: BLE001
            logger.warning("Подсказки недоступны (%s)", error)
            return []

        buckets = response.get("aggregations", {}).get("words", {}).get("buckets", [])
        return [
            _suggestion(bucket["best"]["hits"]["hits"][0]["_source"])
            for bucket in buckets
            if bucket.get("best", {}).get("hits", {}).get("hits")
        ]

    def invalidate(self) -> None:
        """Сбрасывать нечего: словарь живёт в движке и обновляется `manage.py index_ktru`.

        Метод оставлен, потому что порт `SuggestionIndex` используется обеими реализациями, а
        у той, что держит словарь в памяти процесса, сброс — настоящая операция.
        """


def _options(response: dict, name: str) -> list[dict]:
    groups = response.get("suggest", {}).get(name, [])
    return [option for group in groups for option in group.get("options", [])]


def _suggestion(source: dict) -> Suggestion:
    return Suggestion(
        word=source.get("word", ""),
        kind=SuggestionKind(source.get("kind", SuggestionKind.VALUE)),
        source=source.get("source", ""),
        frequency=int(source.get("frequency") or 0),
        is_head=bool(source.get("is_head")),
    )
