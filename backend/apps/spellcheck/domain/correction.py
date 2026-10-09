"""The correction value object and the text rules around it. No Django in this layer."""

from __future__ import annotations

import re
from dataclasses import dataclass

from apps.spellcheck.domain.enums import CorrectionSource
from apps.spellcheck.domain.exceptions import QueryTooLong

#: Long enough for a real product query, short enough that nothing downstream has to defend
#: itself — the LLM prompt and the SymSpell lookup both cost time per character.
MAX_QUERY_LENGTH = 200

#: Cyrillic, Latin, digits and the few separators that occur inside article numbers.
_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+(?:[-/][0-9a-zа-яё]+)*", re.IGNORECASE)


def normalize_query(raw: str) -> str:
    """Collapse whitespace and unify the characters that look identical but are not.

    ``ё``/``е`` is deliberately *not* unified here: it changes what the user sees echoed back in
    the search box. Matching treats them as equal further down, in the index.
    """
    if not isinstance(raw, str):
        return ""
    # A non-breaking space arrives from copy-paste and would otherwise survive `.split()`.
    candidate = raw.replace(" ", " ").replace("’", "'")
    candidate = " ".join(candidate.split())
    if len(candidate) > MAX_QUERY_LENGTH:
        raise QueryTooLong()
    return candidate


def tokenize(text: str) -> list[str]:
    """The words a corrector may touch. Punctuation and spacing are not words."""
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class Correction:
    """What the pipeline decided about one query."""

    original: str
    corrected: str
    source: CorrectionSource

    @property
    def changed(self) -> bool:
        """Whether the user should be told. Case-only differences are not worth a notice."""
        return self.original.casefold() != self.corrected.casefold()

    @staticmethod
    def unchanged(query: str) -> Correction:
        return Correction(original=query, corrected=query, source=CorrectionSource.NONE)

    def then(self, corrected: str, source: CorrectionSource) -> Correction:
        """Apply a later stage, keeping the *original* the user actually typed.

        The notice under the search box compares against what was typed, not against what the
        previous stage produced — otherwise a query fixed twice would claim it was corrected
        from an intermediate string the user never saw.
        """
        if corrected == self.corrected:
            return self
        return Correction(original=self.original, corrected=corrected, source=source)
