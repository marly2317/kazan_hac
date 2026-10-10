"""Перестроение индексов OpenSearch из справочника в Postgres.

Направление одно и только одно: **Postgres → OpenSearch**. Движок — производное
представление, и из этого следует всё, что здесь сделано:

* индекс **пересоздаётся**, а не правится по месту. Инкрементальное обновление пришлось бы
  согласовывать с источником, а согласование — это то, что ломается молча. Пересборка
  справочника категорий хакатона занимает секунды, и правильность достаётся бесплатно;
* схема не версионируется миграциями. Потеря индекса не теряет данных, а расхождение схемы с
  кодом лечится повторным запуском команды;
* справочник читается потоком с `prefetch_related`. N+1 на 1812 позициях определял бы всё
  время импорта, а материализация всех 43 815 значений разом — пик памяти процесса.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Iterator

from apps.autofill.domain.matching import head_word, stem
from apps.autofill.domain.suggest import SuggestionKind
from apps.ktru.infrastructure.models import Position
from apps.ktru.infrastructure.search.client import client, forget
from apps.ktru.infrastructure.search.encoder import encoder
from apps.ktru.infrastructure.search.indexes import (
    PIPELINE,
    POSITIONS,
    TERMS,
    pipeline_body,
    positions_body,
    terms_body,
)
from apps.spellcheck.domain.correction import tokenize

logger = logging.getLogger(__name__)

#: Позиций в одной операции `_bulk`.
#:
#: Ограничение не по числу строк, а по размеру тела запроса: у позиции в среднем две дюжины
#: значений, и 500 позиций — это несколько мегабайт, то есть запрос, который движок принимает
#: не задумываясь. На 2000 уже встречаются тела под сотню мегабайт.
BATCH = 500

#: Строк в одной операции `_bulk` для словаря подсказок — документы там крошечные.
TERMS_BATCH = 2000

#: Слова короче этого в словарь подсказок не попадают: предлоги и обрывки только мешают.
MIN_WORD = 3


def rebuild(*, prefix: str | None = None, progress=None) -> dict[str, int]:
    """Пересобрать оба индекса с нуля. Возвращает «сколько чего записано».

    ``prefix`` подменяет префикс имён — так рядом с рабочим индексом строится индекс под
    замер, не затрагивая тот, которым отвечает сервис.
    """
    connection = client()
    if connection is None:
        raise RuntimeError(
            "OPENSEARCH_URL не задан: индексировать некуда. "
            "Задайте переменную и повторите, либо оставьте поиск на Postgres."
        )

    positions_index = POSITIONS.named(prefix) if prefix else POSITIONS.name
    terms_index = TERMS.named(prefix) if prefix else TERMS.name

    def say(message: str) -> None:
        if progress is not None:
            progress(message)

    # Конвейер слияния живёт на кластере, а не в индексе, и пересоздаётся идемпотентно.
    connection.transport.perform_request(
        "PUT", f"/_search/pipeline/{PIPELINE}", body=pipeline_body()
    )
    say(f"конвейер {PIPELINE}")

    dimension = encoder().dimension
    say(
        f"энкодер: {'включён' if encoder().enabled else 'выключен'}, "
        f"{dimension} измерений"
    )

    for index, body in ((positions_index, positions_body(dimension)), (terms_index, terms_body())):
        if connection.indices.exists(index=index):
            connection.indices.delete(index=index)
        connection.indices.create(index=index, body=body)
        say(f"индекс {index} создан")

    written = _index_positions(connection, positions_index, say)
    terms = _index_terms(connection, terms_index, say)

    # `refresh` явно: иначе только что записанное не находится до следующего автоматического
    # обновления, и первый же запрос после индексации ответил бы пустотой.
    connection.indices.refresh(index=positions_index)
    connection.indices.refresh(index=terms_index)
    forget()

    return {"positions": written, "terms": terms}


def _index_positions(connection, index: str, say) -> int:
    from opensearchpy.helpers import bulk

    total = Position.objects.count()
    written = 0

    for batch in _batched(_positions(), BATCH):
        names = [document["name"] for document in batch]
        vectors = encoder().encode(names)
        if vectors is not None:
            for document, vector in zip(batch, vectors, strict=True):
                document["vector"] = vector

        bulk(
            connection,
            [{"_index": index, "_id": document["code"], "_source": document} for document in batch],
        )
        written += len(batch)
        say(f"позиции: {written}/{total}")

    return written


def _positions() -> Iterator[dict]:
    """Позиции справочника как документы движка."""
    rows = (
        Position.objects.prefetch_related("characteristics__values")
        .order_by("code")
        .iterator(chunk_size=BATCH)
    )
    for position in rows:
        characteristics = list(position.characteristics.all())
        values: list[dict] = []
        for characteristic in characteristics:
            stems = sorted(
                {
                    stem(token)
                    for token in tokenize(characteristic.name)
                    if len(token) >= 3
                }
            )
            for value in characteristic.values.all():
                values.append(
                    {
                        "characteristic_code": characteristic.code,
                        "characteristic": characteristic.name,
                        "stems": stems,
                        "required": characteristic.is_required,
                        "kind": characteristic.type,
                        "value_kind": value.kind,
                        # Нижний регистр здесь, а не в запросе: сравнение текстовых значений
                        # точное, и приводить к одному регистру надо одну из сторон — ту,
                        # которая записывается один раз, а не ту, которая читается на каждый
                        # запрос.
                        "text": value.text.casefold(),
                        "unit": value.unit_name,
                        "exact": _number(value.exact),
                        "minimum": _number(value.minimum),
                        "maximum": _number(value.maximum),
                        "min_inclusive": value.min_inclusive,
                        "max_inclusive": value.max_inclusive,
                    }
                )

        yield {
            "code": position.code,
            "name": position.name,
            "head": head_word(position.name),
            "okpd2_code": position.okpd2_code,
            "okpd2_name": position.okpd2_name,
            "characteristic_names": " ".join(item.name for item in characteristics),
            "values": values,
        }


def _number(value) -> float | None:
    """``Decimal`` в ``float``: движок умеет `double`, а не десятичную дробь.

    Потеря точности здесь безвредна и ограничена: границы в КТРУ заданы с шестью знаками после
    запятой, а `double` различает пятнадцать значащих цифр. Проверка значения на попадание в
    диапазон всё равно делается нашим кодом на `Decimal` — движок отбирает кандидатов, а не
    выносит вердикт о значении.
    """
    return None if value is None else float(value)


def _index_terms(connection, index: str, say) -> int:
    """Словарь справочника: одно вхождение слова в одну фразу — один документ."""
    from opensearchpy.helpers import bulk

    from apps.ktru.infrastructure.models import Characteristic, PermittedValue

    def phrases() -> Iterator[tuple[str, SuggestionKind]]:
        for (name,) in Position.objects.values_list("name").iterator(chunk_size=2000):
            yield name, SuggestionKind.POSITION
        for (name,) in Characteristic.objects.values_list("name").iterator(chunk_size=2000):
            yield name, SuggestionKind.CHARACTERISTIC
        for (text,) in (
            PermittedValue.objects.exclude(text="")
            .values_list("text")
            .iterator(chunk_size=2000)
        ):
            yield text, SuggestionKind.VALUE

    # Частота считается по всему справочнику заранее: она разрешает ничьи в порядке подсказок,
    # и движок её не знает — для него каждый документ равноценен.
    frequency: Counter[tuple[str, str]] = Counter()
    collected: list[tuple[str, SuggestionKind, str, str, bool]] = []

    for phrase, kind in phrases():
        # Тот же отбор, что у индекса в памяти: предлоги и обрывки короче трёх букв только
        # мешают, а число подсказывать нечего — его пользователь пишет сам.
        tokens = [
            token for token in tokenize(phrase) if len(token) >= MIN_WORD and not token.isdigit()
        ]
        for index_in_phrase, token in enumerate(tokens):
            previous = stem(tokens[index_in_phrase - 1]) if index_in_phrase else ""
            is_head = index_in_phrase == 0 and kind is SuggestionKind.POSITION
            frequency[(token, kind)] += 1
            collected.append((token, kind, previous, phrase, is_head))

    documents = [
        {
            "_index": index,
            "_source": {
                "word": word,
                "suggest": {
                    "input": [word],
                    "weight": _weight(kind, is_head, frequency[(word, kind)]),
                },
                "previous": previous,
                "kind": str(kind),
                "source": phrase,
                "is_head": is_head,
                "frequency": frequency[(word, kind)],
            },
        }
        for word, kind, previous, phrase, is_head in collected
    ]

    for batch in _batched(iter(documents), TERMS_BATCH):
        bulk(connection, batch)
    say(f"словарь подсказок: {len(documents)} вхождений")
    return len(documents)


#: Шкала веса в автомате дополнения. Повторяет порядок `Suggestion.rank`: сначала слова,
#: называющие товар, затем вес источника, затем частота.
#:
#: Вес нужен не для показа, а для **отсечения**. Слово лежит в индексе по документу на каждое
#: вхождение, и `skip_duplicates` оставляет из них тот, у которого вес выше; он же решает, кто
#: попадёт в выборку, а кто останется за её границей. Порядок показа при этом всё равно
#: считает домен (`apps.autofill.domain.suggest.rank`) — вес лишь обязан не отрезать то, что
#: домен поставил бы первым.
HEAD_WEIGHT = 1_000_000
KIND_WEIGHT = 1_000

#: Частота выше этого в вес не влезает и не нужна: она разрешает ничьи, а не определяет порядок.
MAX_FREQUENCY = KIND_WEIGHT - 1


def _weight(kind: SuggestionKind, is_head: bool, frequency: int) -> int:
    from apps.autofill.domain.suggest import WEIGHTS

    return (
        (HEAD_WEIGHT if is_head else 0)
        + WEIGHTS[kind] * KIND_WEIGHT
        + min(frequency, MAX_FREQUENCY)
    )


def _batched(source: Iterable, size: int) -> Iterator[list]:
    batch: list = []
    for item in source:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
