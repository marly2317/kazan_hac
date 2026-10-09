"""Search the catalogue, correcting the query first."""

from __future__ import annotations

from dataclasses import dataclass

from apps.catalog.application.dto import SearchProductsInput
from apps.catalog.application.ports import ProductRepository, QueryCorrector
from apps.catalog.domain.entities import SearchPage
from apps.catalog.domain.exceptions import InvalidPageSize
from apps.spellcheck.domain.correction import Correction

MAX_LIMIT = 100


@dataclass(frozen=True)
class SearchProductsResult:
    page: SearchPage
    correction: Correction


class SearchProducts:
    def __init__(self, products: ProductRepository, corrector: QueryCorrector) -> None:
        self._products = products
        self._corrector = corrector

    def execute(self, data: SearchProductsInput) -> SearchProductsResult:
        if not 1 <= data.limit <= MAX_LIMIT or data.offset < 0:
            raise InvalidPageSize()

        correction = (
            self._corrector.execute(data.query)
            if data.correct_typos
            else Correction.unchanged(data.query.strip())
        )

        # An empty query lists the catalogue rather than returning nothing: the products tab
        # opens on it, and an empty results page would read as "there are no products".
        if not correction.corrected:
            page = self._products.list_all(limit=data.limit, offset=data.offset)
            return SearchProductsResult(page=page, correction=correction)

        page = self._products.search(
            correction.corrected, limit=data.limit, offset=data.offset
        )

        # Nothing found for the corrected words, but the user's own words do match something:
        # the "correction" was wrong, so it is withdrawn rather than shown over an empty page.
        if page.total == 0 and correction.changed:
            original = self._products.search(
                correction.original, limit=data.limit, offset=data.offset
            )
            if original.total > 0:
                return SearchProductsResult(
                    page=original, correction=Correction.unchanged(correction.original)
                )

        return SearchProductsResult(page=page, correction=correction)
