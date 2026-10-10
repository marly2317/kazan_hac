"""КТРУ read access for the analyser."""

from __future__ import annotations

from collections import defaultdict

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q

from apps.autofill.domain.entities import PositionCandidate
from apps.autofill.domain.query import SplitQuery
from apps.ktru.domain.entities import Characteristic, PermittedValue, Unit
from apps.ktru.domain.enums import CharacteristicType, ValueKind
from apps.ktru.infrastructure.models import Characteristic as CharacteristicRow
from apps.ktru.infrastructure.models import Position as PositionRow

#: Below this a trigram match is noise; unrelated Russian words share trigrams readily. The
#: score is a sum over the description's words, so the floor scales with how many there are.
NAME_FLOOR_PER_WORD = 0.10

#: The whole-phrase similarity counts for more than any single word: a position whose name is
#: the description is the answer, however the individual words score.
WHOLE_PHRASE_WEIGHT = 3

#: Keep only names scoring at least this fraction of the best match. «Куртка зимняя из кожи»
#: scores 3.9 for that query and «Обувь повседневная с верхом из кожи» scores 1.0 — both share
#: «кожи», but only one is the product. An absolute floor cannot separate them, because what
#: counts as a good score depends on how many words the description has.
RELATIVE_FLOOR = 0.45


class DjangoKtruCatalog:
    """Поиск по Postgres — то, с чего всё начиналось, и запасной путь, когда движка нет.

    Остаётся в проекте не «на всякий случай», а затем, чтобы переезд можно было **измерить**:
    `manage.py bench_retrieval` сравнивает способы на одном наборе запросов, и сравнивать
    можно только с тем, что работает.

    ``whole_text`` выбирает, чем искать: называющей частью описания или описанием целиком.
    Целиком — это прежнее поведение, и оно сохранено ровно для того, чтобы вклад разделения
    запроса был виден отдельно от вклада движка.
    """

    def __init__(self, *, whole_text: bool = False) -> None:
        self._whole_text = whole_text

    def find_positions(self, query: SplitQuery, *, limit: int) -> list[PositionCandidate]:
        """Positions whose name resembles the description.

        Trigram similarity rather than full text: the description is a phrase like
        «куртка зимняя из кожи женская 48 размер», and a `tsquery` built from all of it would
        require every word to appear in the position name. Trigram scores partial overlap,
        which is what a loose description needs.
        """
        text = query.full if self._whole_text else query.name
        words = [word for word in text.split() if len(word) >= 3][:8]
        if not words:
            return []

        # Per-word similarities are **summed**, not maximised. `Greatest` would score a
        # position matching one word of the description exactly as highly as one matching all
        # of them — «Обувь с верхом из кожи» would beat «Куртка зимняя из кожи» for the query
        # «куртка зимняя из кожи», because both contain «кожи». Summing rewards covering more
        # of what the user wrote, which is the thing that actually indicates the right position.
        score = TrigramSimilarity("name", text) * WHOLE_PHRASE_WEIGHT
        for word in words:
            score = score + TrigramSimilarity("name", word)

        rows = (
            PositionRow.objects.annotate(score=score)
            .filter(Q(score__gte=NAME_FLOOR_PER_WORD * len(words)))
            .order_by("-score", "code")[:limit]
            .values_list("code", "name", "okpd2_code", "score")
        )
        candidates = [
            PositionCandidate(code=code, name=name, okpd2_code=okpd2, score=float(value))
            for code, name, okpd2, value in rows
        ]
        if not candidates:
            return []

        cutoff = candidates[0].score * RELATIVE_FLOOR
        return [candidate for candidate in candidates if candidate.score >= cutoff]

    def characteristics_for(self, codes: list[str]) -> dict[str, list[Characteristic]]:
        """Every characteristic of the given positions, with permitted values attached.

        One query with ``prefetch_related`` rather than one per position: the candidate pool is
        hundreds of positions and the N+1 would dominate the whole request.
        """
        if not codes:
            return {}

        rows = (
            CharacteristicRow.objects.filter(position_id__in=codes)
            .prefetch_related("values")
            .order_by("position_id", "id")
        )

        result: dict[str, list[Characteristic]] = defaultdict(list)
        for row in rows:
            result[row.position_id].append(
                Characteristic(
                    code=row.code,
                    name=row.name,
                    type=CharacteristicType(row.type),
                    is_required=row.is_required,
                    values=[_value(value) for value in row.values.all()],
                )
            )
        return dict(result)


def _value(row) -> PermittedValue:
    unit = Unit(code=row.unit_code, name=row.unit_name) if row.unit_name else None
    return PermittedValue(
        kind=ValueKind(row.kind),
        text=row.text,
        exact=row.exact,
        minimum=row.minimum,
        maximum=row.maximum,
        min_inclusive=row.min_inclusive,
        max_inclusive=row.max_inclusive,
        unit=unit,
    )
