"""Input dataclasses for the catalogue use cases."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchProductsInput:
    query: str
    limit: int = 20
    offset: int = 0
    #: ``False`` is what the "search for the original instead" link sends: the user has seen the
    #: correction and rejected it, so correcting again would undo their choice on every page.
    correct_typos: bool = True
