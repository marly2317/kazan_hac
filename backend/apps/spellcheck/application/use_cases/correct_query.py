"""Correct a search query."""

from __future__ import annotations

import logging

from apps.spellcheck.application.ports import QueryRewriter
from apps.spellcheck.domain.correction import Correction, normalize_query
from apps.spellcheck.domain.enums import CorrectionSource

logger = logging.getLogger(__name__)


class CorrectQuery:
    """Runs the rewriters in order and reports what the first effective one did.

    The order is cheapest-first and is the whole point of the design: a keyboard-layout slip is
    decided by a lookup table in microseconds, a real typo by an in-memory index in under a
    millisecond, and only a query that both of those left untouched is worth a network call to a
    language model. Running the model on every keystroke-perfect query would spend money and
    latency to return the input unchanged.

    The first stage that changes the query wins; later stages are not run. A dictionary fix and
    an LLM rewrite of the *same* words disagree more often than they agree, and the deterministic
    one is the one we can explain to the user.
    """

    def __init__(self, stages: list[tuple[CorrectionSource, QueryRewriter]]) -> None:
        self._stages = stages

    def execute(self, raw_query: str) -> Correction:
        query = normalize_query(raw_query)
        result = Correction.unchanged(query)
        if not query:
            return result

        for source, rewriter in self._stages:
            try:
                rewritten = rewriter.rewrite(query)
            except Exception:
                # A failing stage must never fail the search: the user gets their own words back
                # and the results that match them.
                logger.exception("Correction stage %s failed", source.value)
                continue

            if rewritten and rewritten != query:
                return result.then(rewritten, source)

        return result
