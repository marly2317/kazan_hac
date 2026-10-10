"""Способы поиска позиций КТРУ, которые можно сравнить между собой.

Существуют ради замера (`manage.py bench_retrieval`), а не ради переключения в бою: выбранный
способ живёт в `catalog.py` и подключается в `config/container.py`. Здесь важно, что все они
отвечают одним и тем же — списком `(код, наименование, вес)`, — и потому сравнимы.

То, что такой файл вообще удалось написать, и есть ответ на вопрос «насколько мы привязаны к
Postgres»: поиск спрятан за портом `KtruCatalog`, и смена способа — это один класс плюс строка
в композиционном корне. Переезд на другой движок не затрагивает ни сужение, ни проверку
значений, ни интерфейс.
"""

from __future__ import annotations

from typing import Protocol

from django.db.models import Q

from apps.ktru.infrastructure.models import Position


class SearchBackend(Protocol):
    def search(self, query: str, limit: int) -> list[tuple[str, str, float]]: ...

    def available(self) -> bool: ...

    def why_not(self) -> str: ...


class TrigramSum:
    """Текущий способ: сумма триграммных сходств по словам, плюс фраза целиком.

    Сортировка по вычисляемому выражению, поэтому GIN-индекс здесь не применяется — это
    последовательный проход по таблице. На 1812 позициях он стоит ~35 мс; смысл замера в том,
    чтобы узнать, чего это будет стоить дальше и есть ли смысл менять движок.
    """

    label = "Postgres pg_trgm (сумма сходств) — текущий"

    def available(self) -> bool:
        return True

    def why_not(self) -> str:
        return ""

    def search(self, query: str, limit: int) -> list[tuple[str, str, float]]:
        from apps.autofill.infrastructure.catalog import DjangoKtruCatalog

        return [
            (c.code, c.name, c.score)
            for c in DjangoKtruCatalog().find_positions(query, limit=limit)
        ]


class TrigramPrefiltered:
    """То же ранжирование, но кандидаты сперва отбираются индексируемым оператором ``%``.

    ``%`` — это «похоже по триграммам» с порогом `pg_trgm.similarity_threshold`, и он **умеет**
    пользоваться GIN-индексом. Ранжирование остаётся прежним, но считается уже не по всей
    таблице, а по тому, что отобрал индекс.

    Смысл в том, чтобы отделить «нам нужен другой движок» от «нам нужен нормальный запрос»:
    если выигрыш заметный, разговор об OpenSearch можно отложить до настоящего роста данных.
    """

    label = "Postgres pg_trgm + индексируемый предфильтр"

    def available(self) -> bool:
        return True

    def why_not(self) -> str:
        return ""

    def search(self, query: str, limit: int) -> list[tuple[str, str, float]]:
        from django.contrib.postgres.search import TrigramSimilarity

        from apps.autofill.infrastructure.catalog import (
            NAME_FLOOR_PER_WORD,
            RELATIVE_FLOOR,
            WHOLE_PHRASE_WEIGHT,
        )

        words = [word for word in query.split() if len(word) >= 3][:8]
        if not words:
            return []

        # Предфильтр: имя должно быть «похоже» хоть на одно слово запроса или на фразу целиком.
        # Это условие в WHERE, а не в ORDER BY, поэтому планировщик может взять GIN-индекс.
        matches = Q(name__trigram_similar=query)
        for word in words:
            matches |= Q(name__trigram_similar=word)

        score = TrigramSimilarity("name", query) * WHOLE_PHRASE_WEIGHT
        for word in words:
            score = score + TrigramSimilarity("name", word)

        rows = (
            Position.objects.filter(matches)
            .annotate(score=score)
            .filter(score__gte=NAME_FLOOR_PER_WORD * len(words))
            .order_by("-score", "code")[:limit]
            .values_list("code", "name", "score")
        )
        found = [(code, name, float(value)) for code, name, value in rows]
        if not found:
            return []
        cutoff = found[0][2] * RELATIVE_FLOOR
        return [row for row in found if row[2] >= cutoff]


class FullTextRussian:
    """Полнотекстовый поиск Postgres со словарём русского языка.

    Ближайший бесплатный аналог того, что в OpenSearch называется русским анализатором:
    `to_tsvector('russian', …)` приводит слова к основам по словарю, а не по первым пяти
    буквам. Именно это должно вытянуть корзину ``morphology`` — и если вытянет, то
    морфология в нашем случае не повод ставить отдельный поисковый кластер.

    Составные слова («шлифовальная машина» против «Шлифмашина») так не решаются: для них
    нужен либо декомпаундинг, либо словарь синонимов, либо семантика.
    """

    label = "Postgres full-text (русский словарь)"

    def available(self) -> bool:
        return True

    def why_not(self) -> str:
        return ""

    def search(self, query: str, limit: int) -> list[tuple[str, str, float]]:
        from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

        vector = SearchVector("name", config="russian")
        # `websearch` терпим к произвольной фразе: обычный `plainto_tsquery` требует все слова
        # сразу, а описание — это не запрос из слов, которые обязаны встретиться все.
        search = SearchQuery(query, config="russian", search_type="websearch")
        rows = (
            Position.objects.annotate(rank=SearchRank(vector, search))
            .filter(rank__gt=0)
            .order_by("-rank", "code")[:limit]
            .values_list("code", "name", "rank")
        )
        return [(code, name, float(rank)) for code, name, rank in rows]


class HybridFtsTrigram:
    """Отбор кандидатов индексом, ранжирование — как сейчас.

    Проверяет, обязательно ли ради роста данных ставить отдельный поисковый кластер. Узкое
    место нынешнего способа не в качестве, а в том, что сумма триграммных сходств считается
    **для каждой строки таблицы**: сортировка по вычисляемому выражению индексом не
    покрывается. На 1812 позициях это 30 мс, на 60 тысячах — полсекунды.

    Здесь кандидатов отбирает полнотекстовый поиск с GIN-индексом по `tsvector` (он быстрый и
    масштабируется), а дальше те же несколько сотен строк пересчитываются прежней формулой уже
    в Python. Качество ранжирования остаётся нашим, а стоимость перестаёт зависеть от размера
    таблицы.
    """

    label = "Postgres FTS (отбор) + триграммы (ранжирование)"

    #: Сколько строк отдать на пересчёт. Шире, чем показываем, но достаточно узко, чтобы
    #: пересчёт стоил микросекунды.
    POOL = 400

    def available(self) -> bool:
        return True

    def why_not(self) -> str:
        return ""

    def search(self, query: str, limit: int) -> list[tuple[str, str, float]]:
        from django.contrib.postgres.search import SearchQuery, SearchVector

        from apps.autofill.domain.matching import trigrams
        from apps.autofill.infrastructure.catalog import RELATIVE_FLOOR, WHOLE_PHRASE_WEIGHT

        words = [word for word in query.split() if len(word) >= 3][:8]
        if not words:
            return []

        vector = SearchVector("name", config="russian")
        search = SearchQuery(query, config="russian", search_type="websearch")
        rows = list(
            Position.objects.annotate(vector=vector)
            .filter(vector=search)
            .values_list("code", "name")[: self.POOL]
        )
        if not rows:
            return []

        # Та же формула, что и в бою, только над уже отобранными строками.
        def similarity(text: str, name: str) -> float:
            first, second = trigrams(text), trigrams(name)
            union = first | second
            return len(first & second) / len(union) if union else 0.0

        scored = []
        for code, name in rows:
            value = similarity(query, name) * WHOLE_PHRASE_WEIGHT
            for word in words:
                value += similarity(word, name)
            scored.append((code, name, value))

        scored.sort(key=lambda row: (-row[2], row[0]))
        cutoff = scored[0][2] * RELATIVE_FLOOR
        return [row for row in scored if row[2] >= cutoff][:limit]


def all_backends() -> dict[str, SearchBackend]:
    return {
        "trgm_sum": TrigramSum(),
        "trgm_prefilter": TrigramPrefiltered(),
        "fts_russian": FullTextRussian(),
        "hybrid": HybridFtsTrigram(),
    }
