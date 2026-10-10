"""Справочник КТРУ через OpenSearch: отбор по наименованию и сужение по характеристикам.

Реализует порты `KtruCatalog` и `CatalogNarrowing`. Запросы собираются чистыми функциями в
`application/retrieval.py` и `application/narrowing.py` — здесь только обращение к движку и
разбор ответа, и это намеренно: тело запроса можно проверить тестом без сервера, а в этом файле
проверять нечего, кроме того, что ответ разобран.

## Два обращения, как и было

Раньше их тоже было два: поиск позиций и выборка их характеристик из Postgres. Теперь:

1. **отбор** — позиции по наименованию, вместе с их допустимыми значениями в `_source`.
   Значения нужны тут же: распознавание характеристик в описании работает по допустимым
   значениям кандидатов, то есть второй запрос за ними всё равно пришлось бы делать;
2. **сужение** — те же условия плюс фильтры по распознанным характеристикам, причём по
   **всему индексу**, а не по отобранному пулу. Это и есть выигрыш, которого перебором не
   получить: позиция, чьё наименование оказалось на 301-м месте, раньше не могла быть
   восстановлена никакими совпавшими характеристиками.

Шаги сужения считаются одной операцией `_msearch`: по запросу на префикс фильтров, каждый без
документов. Накладные расходы — один сетевой обмен на всё сужение, а не на шаг.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from apps.autofill.application import narrowing as filters
from apps.autofill.application.ports import Narrowing
from apps.autofill.application.retrieval import search_body
from apps.autofill.domain.entities import ExtractedCharacteristic, PositionCandidate
from apps.autofill.domain.query import SplitQuery
from apps.ktru.domain.entities import Characteristic, PermittedValue, Unit
from apps.ktru.domain.enums import CharacteristicType, ValueKind
from apps.ktru.infrastructure.search.client import client
from apps.ktru.infrastructure.search.encoder import encoder
from apps.ktru.infrastructure.search.indexes import PIPELINE, POSITIONS

logger = logging.getLogger(__name__)


class OpenSearchKtruCatalog:
    """Поиск позиций и сужение кандидатов средствами движка."""

    def __init__(self, index: str | None = None) -> None:
        # Имя индекса параметром — так замер строит и опрашивает индекс рядом с рабочим, не
        # затрагивая тот, которым отвечает сервис.
        self._index = index
        # Допустимые значения кандидатов, пришедшие вместе с ними в `_source`. Нужны затем,
        # что `characteristics_for` в этой реализации — не запрос, а чтение уже полученного:
        # второе обращение за тем же было бы платой за то, что уже привезли.
        self._values: dict[str, list[dict]] = {}

    @property
    def index(self) -> str:
        return self._index or POSITIONS.name

    # --- отбор --------------------------------------------------------------------------

    def find_positions(self, query: SplitQuery, *, limit: int) -> list[PositionCandidate]:
        """Позиции, чьё наименование похоже на **называющую часть** описания."""
        response = self._search(search_body(query, self._vector(query), limit=limit))
        hits = response.get("hits", {}).get("hits", [])

        self._values = {
            hit["_source"]["code"]: hit["_source"].get("values", []) for hit in hits
        }
        return [_candidate(hit) for hit in hits]

    def characteristics_for(self, codes: list[str]) -> dict[str, list[Characteristic]]:
        """Характеристики позиций — из того, что уже приехало вместе с кандидатами.

        Обращения к движку здесь нет. Если спросили о позиции, которой в последнем ответе не
        было, она просто отсутствует в результате: вызывающая сторона и так обязана это
        выдерживать — у позиции может не быть ни одной характеристики.
        """
        return {
            code: _characteristics(self._values[code])
            for code in codes
            if code in self._values
        }

    # --- сужение ------------------------------------------------------------------------

    def narrow(
        self,
        query: SplitQuery,
        items: list[ExtractedCharacteristic],
        *,
        within: list[str],
        limit: int,
    ) -> Narrowing:
        """Позиции, которым не противоречит ни одна распознанная характеристика.

        Правило — то же, что в `_verdict`: выбывает только явное противоречие, молчание
        позиции виной не считается. Выражено условиями в `application/narrowing.py`.

        ``within`` ограничивает сужение позициями с разрешённым наименованием. Порядок здесь
        не технический: наименование определяется первым, и характеристики разбирают то, что
        им оставлено, — внутри группы одноимённых позиций, различающихся только ими.
        """
        vector = self._vector(query)
        scope = [filters.within(within)]
        keeps = [filters.keeps(item) for item in items]
        supports = [filters.supports(item, named=True) for item in items]

        steps = self._count_steps(scope, keeps)

        response = self._search(
            search_body(
                query,
                vector,
                limit=limit,
                filters=scope + keeps,
                supports=supports,
            )
        )
        hits = response.get("hits", {}).get("hits", [])
        self._values = {
            hit["_source"]["code"]: hit["_source"].get("values", []) for hit in hits
        }

        return Narrowing(
            candidates=[_candidate(hit, with_support=True) for hit in hits],
            steps=[
                (item.name, before, after)
                for item, (before, after) in zip(items, steps, strict=True)
            ],
            total=response.get("hits", {}).get("total", {}).get("value", len(hits)),
        )

    def _count_steps(
        self, scope: list[dict], keeps: list[dict]
    ) -> list[tuple[int, int]]:
        """«Было — стало» на каждом шаге сужения, одной операцией `_msearch`.

        Запрос по наименованию здесь **не участвует**, и это не упрощение. Наименование уже
        разрешено, и ``scope`` — это его результат, перечисленный кодами; добавить к нему
        поиск по наименованию значило бы спросить то же самое дважды.

        Тем более не участвует ветвь kNN. Она возвращает ``k`` ближайших соседей, то есть
        **ограничивает** число найденного сверху, — и счётчик показывал бы не «сколько
        позиций отвечает условию», а «сколько из них попало в первые k». На шаге сужения это
        прямая неправда, а шаг сужения — то, что пользователь читает.

        Поэтому считает обычный `bool` с фильтрами. Заодно он не нуждается в конвейере
        слияния, которого `_msearch` и не принимает.
        """
        if not keeps:
            return []

        connection = client()
        if connection is None:
            return [(0, 0)] * len(keeps)

        payload = "".join(
            json.dumps(line, ensure_ascii=False) + "\n"
            for taken in range(len(keeps) + 1)
            for line in (
                {"index": self.index},
                {
                    "size": 0,
                    # Точное число, а не оценка: шаг сужения показывает «было 14 — стало 2», и
                    # приблизительное «больше 10 000» там было бы бессмысленно.
                    "track_total_hits": True,
                    "query": {"bool": {"filter": scope + keeps[:taken]}},
                },
            )
        )
        response = connection.msearch(
            body=payload, headers={"content-type": "application/x-ndjson"}
        )
        totals = [
            item.get("hits", {}).get("total", {}).get("value", 0)
            for item in response.get("responses", [])
        ]
        # Сдвиг на единицу: шаг i — это «сколько было с i фильтрами» → «сколько стало с i+1».
        return list(zip(totals, totals[1:], strict=False))

    # --- обращение к движку ---------------------------------------------------------------

    def _search(self, body: dict) -> dict:
        connection = client()
        if connection is None:
            return {}
        # Конвейер указывается на запросе, а не на индексе: по тому же индексу идёт замер
        # других способов, и зашитый в индекс конвейер слияния применялся бы и к ним.
        return connection.search(index=self.index, body=body, search_pipeline=PIPELINE)

    @staticmethod
    def _vector(query: SplitQuery) -> list[float] | None:
        """Вектор **называющей части**, а не описания целиком.

        Числа и имена характеристик в наименованиях позиций не встречаются, и вектор описания
        вместе с ними указывал бы в сторону от того, что лежит в индексе: там векторы
        наименований.
        """
        return encoder().encode_one(query.name)


def _candidate(hit: dict, *, with_support: bool = False) -> PositionCandidate:
    source = hit["_source"]
    matched = hit.get("matched_queries") or []
    return PositionCandidate(
        code=source["code"],
        name=source["name"],
        okpd2_code=source.get("okpd2_code", ""),
        # Оценка движка — это **место** после RRF-слияния, а не похожесть. Порядок она задаёт
        # верно, но судить по ней «нашлось или нет» нельзя: решение о воздержании принимается
        # по словам наименования в `domain/matching.py`, и это свойство переезд не затронул.
        score=float(hit.get("_score") or 0.0),
        support=(
            sum(1 for name in matched if name.startswith(filters.SUPPORT_PREFIX))
            if with_support
            else 0
        ),
    )


def _characteristics(values: list[dict]) -> list[Characteristic]:
    """Вложенные документы значений — обратно в доменные характеристики.

    Индекс хранит по документу на **значение**, потому что иначе условие сужения стало бы
    вложенным в двух уровнях (см. `search/indexes.py`). Здесь плоский список собирается назад
    в характеристики: дальше работает домен, а он знает только их.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    order: list[str] = []
    for value in values:
        code = value.get("characteristic_code", "")
        if code not in grouped:
            order.append(code)
        grouped[code].append(value)

    result: list[Characteristic] = []
    for code in order:
        group = grouped[code]
        first = group[0]
        result.append(
            Characteristic(
                code=code,
                name=first.get("characteristic", ""),
                type=CharacteristicType(first.get("kind", CharacteristicType.QUALITATIVE)),
                is_required=bool(first.get("required")),
                values=[_value(value) for value in group],
            )
        )
    return result


def _value(row: dict) -> PermittedValue:
    unit_name = row.get("unit") or ""
    return PermittedValue(
        kind=ValueKind(row.get("value_kind", ValueKind.TEXT)),
        # Регистр в индексе приведён к нижнему — там сравнение точное. Здесь значение попадает
        # в объяснение для пользователя, но взять исходное написание неоткуда: подменять его
        # догадкой о заглавной букве хуже, чем показать как хранится.
        text=row.get("text") or "",
        exact=_decimal(row.get("exact")),
        minimum=_decimal(row.get("minimum")),
        maximum=_decimal(row.get("maximum")),
        min_inclusive=bool(row.get("min_inclusive", True)),
        max_inclusive=bool(row.get("max_inclusive", True)),
        # Кода ОКЕИ в индексе нет: он нигде не используется, а имя единицы — используется
        # всюду, от конверсии до объяснения.
        unit=Unit(code="", name=unit_name) if unit_name else None,
    )


def _decimal(value) -> Decimal | None:
    """``double`` из индекса — обратно в ``Decimal``.

    Через строку, а не `Decimal(float)`: прямое преобразование даёт двоичное приближение
    (`Decimal(0.1)` — это 0.1000000000000000055…), и сравнение с границей КТРУ начинает
    зависеть от представления. Строка возвращает то десятичное число, которое записывалось.
    """
    if value is None:
        return None
    try:
        return Decimal(repr(float(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None
