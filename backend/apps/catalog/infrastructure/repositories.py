"""Postgres-backed product search.

Two signals, combined:

* **Full-text search** with the ``russian`` configuration, which stems (``ноутбука`` matches
  ``ноутбук``) and drops stop words. Precise, and worthless when a word is still misspelled
  after correction.
* **Trigram similarity** on the name, which matches by character overlap and so survives the
  typos the correction pipeline did not catch.

Full text decides relevance where it applies; trigram is the floor that keeps a near-miss query
from returning an empty page. Correction runs *before* this, so in practice most queries are
answered by the first signal alone.
"""

from __future__ import annotations

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector, TrigramSimilarity
from django.db.models import F, FloatField, Q, QuerySet, Value
from django.db.models.functions import Greatest

from apps.catalog.domain.entities import SearchHit, SearchPage
from apps.catalog.domain.exceptions import ProductNotFound
from apps.catalog.infrastructure.mappers import to_entity
from apps.catalog.infrastructure.models import Product as ProductModel
from core.infrastructure.repository import Repository

#: The text columns that make up the stored vector. Weights are PostgreSQL's A–D: a hit in the
#: name outranks the same word buried in a description.
SEARCH_FIELDS = (("name", "A"), ("category", "B"), ("description", "C"))

#: Below this, a trigram match is noise — unrelated words share trigrams readily.
TRIGRAM_FLOOR = 0.22


def build_search_vector() -> SearchVector:
    vector = None
    for field, weight in SEARCH_FIELDS:
        part = SearchVector(field, weight=weight, config="russian")
        vector = part if vector is None else vector + part
    return vector


class DjangoProductRepository(Repository):
    not_found_error = ProductNotFound

    def _page(self, queryset: QuerySet, *, limit: int, offset: int) -> SearchPage:
        # Counted before slicing, so the UI can say how many matched rather than how many fit
        # on this page.
        total = queryset.count()
        rows = list(queryset[offset : offset + limit])
        hits = [
            SearchHit(product=to_entity(row), rank=float(getattr(row, "rank", 0.0) or 0.0))
            for row in rows
        ]
        return SearchPage(hits=hits, total=total, limit=limit, offset=offset)

    def list_all(self, *, limit: int, offset: int) -> SearchPage:
        queryset = ProductModel.objects.all().annotate(
            rank=Value(0.0, output_field=FloatField())
        )
        return self._page(queryset, limit=limit, offset=offset)

    def search(self, query: str, *, limit: int, offset: int) -> SearchPage:
        text = query.strip()
        if not text:
            return self.list_all(limit=limit, offset=offset)

        # `websearch` accepts what people actually type — quoted phrases, `-excluded` — without
        # raising on the punctuation that makes `plainto_tsquery` fail.
        ts_query = SearchQuery(text, config="russian", search_type="websearch")

        queryset = (
            ProductModel.objects.annotate(
                text_rank=SearchRank(F("search_vector"), ts_query),
                name_similarity=TrigramSimilarity("name", text),
            )
            .annotate(rank=Greatest("text_rank", "name_similarity"))
            .filter(Q(search_vector=ts_query) | Q(name_similarity__gte=TRIGRAM_FLOOR))
            # `-rank` then `name`: ties would otherwise come back in whatever order the planner
            # chose, so the same query could paginate inconsistently.
            .order_by("-rank", "name")
        )
        return self._page(queryset, limit=limit, offset=offset)

    def refresh_search_vectors(self) -> int:
        """Recompute the stored vectors. Called after a bulk import."""
        return ProductModel.objects.update(search_vector=build_search_vector())
