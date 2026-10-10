"""Словарь справочника, разделённый по роли слова.

Отвечает на единственный вопрос, который нельзя решить по тексту описания: **чем работает это
слово — названием товара или его описанием?** «Кожи» в «куртка зимняя из кожи» — часть
наименования позиции («Куртка зимняя из кожи» есть в справочнике ровно в таком виде); «кожи» в
«перчатки из кожи» — значение характеристики «Материал». Одно слово, две роли, и различает их
только справочник.

Отсюда два множества основ:

* `names` — слова из наименований позиций;
* `describes` — слова из имён характеристик и допустимых значений.

Они **пересекаются**, и это не дефект: «зимняя» есть и там, и там. Решает это не словарь, а
порядок правил в :func:`apps.autofill.domain.query.split` — наименование побеждает, потому что
поиск позиции идёт по наименованиям, и выбросить из запроса слово, которое в них встречается,
значило бы ослабить его без выгоды.

Реализует порт `Lexicon`, объявленный модулем `autofill`; соединяет их композиционный корень,
поэтому ни один из модулей не знает о внутренностях другого.

## Почему не в OpenSearch

Это словарь, а не поиск. Движок мог бы отвечать на «встречается ли основа в поле `name`»
запросом `terms`, но спрашивать его по слову на каждый разбор описания значило бы заменить
обращение к множеству в памяти на обращение по сети — три порядка разницы ради сведений,
которые меняются раз в жизни стенда. По той же причине, по которой подсказки строились на
`bisect`: сетевой вызов стоит дороже самого поиска, когда искать нужно в пятидесяти килобайтах.
"""

from __future__ import annotations

import logging

from django.core.cache import cache

from apps.ktru.infrastructure.models import Characteristic, PermittedValue, Position
from apps.spellcheck.domain.correction import tokenize

logger = logging.getLogger(__name__)

NAMES_KEY = "ktru:lexicon:names:v1"
DESCRIBES_KEY = "ktru:lexicon:describes:v1"
CACHE_TTL = 60 * 60 * 24

#: Короче этого слово роль не определяет: предлог или частица встречается всюду.
MIN_LENGTH = 3

#: Сколько букв слова образуют ключ словаря.
#:
#: Свой, а не `matching.STEM_LENGTH`, и это не дублирование — у них разные задачи. Там нужно
#: сравнить **два** слова и решить, не одно ли это слово: пять букв достаточно, потому что
#: сравниваются конкретные два. Здесь нужно проверить принадлежность к множеству из тысяч
#: слов, и на пяти буквах множество начинает ловить лишнее: «размер» совпадало с
#: «размещаемого» из наименования позиции и поэтому считалось словом наименования — ровно
#: наоборот тому, что нужно.
#:
#: Шесть, а не больше: русский склоняет окончание, и «кожи» против «кожа» должны остаться
#: одним словом. Слова короче ключа берутся целиком.
KEY_LENGTH = 6


def key(word: str) -> str:
    """Ключ слова в этом словаре. См. :data:`KEY_LENGTH`."""
    return word.casefold()[:KEY_LENGTH]


class KtruLexicon:
    """Основы слов справочника, разделённые по месту, где слово встречается.

    Строится обходом всего справочника, поэтому кэшируется — по той же причине и так же, как
    индексы корректора: обслуживает каждый разбор описания, а меняется только при импорте.
    """

    def __init__(self) -> None:
        self._names: frozenset[str] | None = None
        self._describes: frozenset[str] | None = None

    @staticmethod
    def key(word: str) -> str:
        """Ключ слова. Словарь спрашивают им, а не сырым словом."""
        return key(word)

    def names(self, word: str) -> bool:
        if self._names is None:
            self._names = self._load(NAMES_KEY, self._collect_names)
        return word in self._names

    def describes(self, word: str) -> bool:
        if self._describes is None:
            self._describes = self._load(DESCRIBES_KEY, self._collect_describes)
        return word in self._describes

    @staticmethod
    def _load(key: str, collect) -> frozenset[str]:
        cached = cache.get(key)
        if cached is not None:
            return frozenset(cached)
        stems = collect()
        cache.set(key, list(stems), CACHE_TTL)
        logger.info("Словарь %s: %s основ", key, len(stems))
        return stems

    @staticmethod
    def _collect_names() -> frozenset[str]:
        return frozenset(
            key(token)
            for (name,) in Position.objects.values_list("name").iterator(chunk_size=2000)
            for token in tokenize(name)
            if len(token) >= MIN_LENGTH and not token.isdigit()
        )

    @staticmethod
    def _collect_describes() -> frozenset[str]:
        """Слова, которые в КТРУ **описывают** товар, а не называют его.

        Имена характеристик и названия единиц ОКЕИ — и **не** допустимые значения.

        Значения пришлось исключить по наблюдению: «Отвертки» в КТРУ — это допустимое значение
        некоторой характеристики, и пока значения считались признаком, слово «отвертки» уходило
        в характеристики. От описания «отвертки крестовая PH2 длина 150 мм» оставалось
        наименование «PH2», по нему находилось что-нибудь похожее, и вместо честного «такого в
        справочнике нет» выходил уверенный неверный ответ. В допустимых значениях лежат
        названия товаров и их материалов — «Отвертки», «Пластик», «Кожа», — то есть ровно та
        лексика, которой товар называют.
        """
        collected: set[str] = set()

        def add(text: str) -> None:
            collected.update(
                key(token)
                for token in tokenize(text or "")
                if len(token) >= MIN_LENGTH and not token.isdigit()
            )

        for (name,) in Characteristic.objects.values_list("name").iterator(chunk_size=2000):
            add(name)
        for (unit,) in (
            PermittedValue.objects.exclude(unit_name="")
            .values_list("unit_name")
            .iterator(chunk_size=2000)
        ):
            add(unit)

        return frozenset(collected)

    @staticmethod
    def invalidate() -> None:
        cache.delete(NAMES_KEY)
        cache.delete(DESCRIBES_KEY)
