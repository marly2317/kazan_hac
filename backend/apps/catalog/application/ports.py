"""Ports the catalogue use cases depend on."""

from __future__ import annotations

from typing import Protocol

from apps.catalog.domain.entities import SearchPage
from apps.spellcheck.domain.correction import Correction


class ProductRepository(Protocol):
    """The product source.

    Declared as a port precisely because the source is expected to change: today it is the
    Postgres table this module owns, tomorrow it may be an external catalogue or an index.
    Only ``infrastructure`` changes when it does.
    """

    def search(self, query: str, *, limit: int, offset: int) -> SearchPage: ...

    def list_all(self, *, limit: int, offset: int) -> SearchPage: ...


class QueryCorrector(Protocol):
    """Typo correction, as the catalogue sees it.

    The catalogue does not know *how* a query gets corrected — the ``spellcheck`` module owns
    that, and the container wires one to the other. This is the only seam between the two
    modules, which is what keeps them independently replaceable.
    """

    def execute(self, raw_query: str) -> Correction: ...
