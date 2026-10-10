"""Dictionary correction with SymSpell, over the catalogue's own vocabulary.

SymSpell (Wolf Garbe's symmetric-delete algorithm, via ``symspellpy``) is the standard
open-source answer to "fix this word, fast": it precomputes deletions instead of generating
candidates per lookup, which makes a correction a dictionary hit rather than a scan. For a
search box — where the budget is a millisecond and the dictionary is tens of thousands of
words — it is the right tool.

What matters more than the algorithm is the *dictionary*. It is built from the catalogue being
searched, not from a general Russian word list, because the goal is not "a correctly spelled
Russian word" but "a word that will actually match a product".
"""

from __future__ import annotations

import logging
import threading

from symspellpy import SymSpell, Verbosity

from apps.spellcheck.application.ports import VocabularySource
from apps.spellcheck.domain.correction import is_correctable, tokenize
from apps.spellcheck.domain.substitution import replace_token

logger = logging.getLogger(__name__)

#: Edit distance the index is built for. 2 covers the overwhelming majority of real typos;
#: 3 explodes the precomputed deletions and starts "correcting" distinct words into each other.
MAX_EDIT_DISTANCE = 2

#: Below this length an edit of 2 is most of the word, so corrections become arbitrary
#: (``ток`` → ``сок`` → ``бок``). Short tokens are only fixed at distance 1.
#:
#: Raised from 5 to 7 after «крестовая» → «ростовая»: at distance 2 a nine-letter word that is
#: simply absent from the reference reaches a different word entirely, and the result is not a
#: corrected query but a different query. Two edits are only safe once the remaining letters
#: still carry the word.
SHORT_TOKEN_LENGTH = 7


class SymSpellRewriter:
    """Per-token correction against the catalogue vocabulary.

    The index is built lazily on first use and then reused: building it walks every product, so
    doing it at import time would make a migration or a management command pay for a search
    feature it never calls.
    """

    def __init__(self, vocabulary: VocabularySource) -> None:
        self._vocabulary = vocabulary
        self._index: SymSpell | None = None
        self._bigrams: dict[str, int] | None = None
        # Gunicorn serves requests on threads; two of them racing to build the index would do
        # the work twice and publish a half-filled one.
        self._lock = threading.Lock()

    def _build(self) -> SymSpell:
        index = SymSpell(max_dictionary_edit_distance=MAX_EDIT_DISTANCE, prefix_length=7)
        count = 0
        for word, frequency in self._vocabulary.terms():
            index.create_dictionary_entry(word, frequency)
            count += 1
        logger.info("SymSpell index built from %s catalogue terms", count)
        return index

    def _ensure_index(self) -> SymSpell:
        if self._index is None:
            with self._lock:
                if self._index is None:
                    self._index = self._build()
        return self._index

    def _bigram_counts(self) -> dict[str, int]:
        if self._bigrams is None:
            with self._lock:
                if self._bigrams is None:
                    source = getattr(self._vocabulary, "bigrams", None)
                    self._bigrams = dict(source()) if callable(source) else {}
        return self._bigrams

    def invalidate(self) -> None:
        """Drop the index so the next lookup rebuilds it — after the reference changes."""
        with self._lock:
            self._index = None
            self._bigrams = None

    def _candidates(self, index: SymSpell, token: str) -> list[str]:
        """Every correction tied for closest. Empty when the token is already a known word."""
        distance = MAX_EDIT_DISTANCE if len(token) >= SHORT_TOKEN_LENGTH else 1
        suggestions = index.lookup(
            token, Verbosity.ALL, max_edit_distance=distance, include_unknown=False
        )
        if not suggestions:
            return []

        best = min(suggestion.distance for suggestion in suggestions)
        # `best == 0` means the token is in the vocabulary as typed: never "correct" it.
        if best == 0:
            return []
        # Ordered by frequency, which is the tiebreak when context says nothing.
        tied = [s.term for s in suggestions if s.distance == best]
        # The first letter has to survive. A mistyped word almost always starts correctly —
        # the finger lands wrong *inside* the word — so a candidate beginning with a different
        # letter is not this word misspelled, it is another word that happens to be close.
        # This is the rule that stops «крестовая» → «ростовая» and «на» → «дна»: without it,
        # any word absent from the reference is rewritten into the nearest word present, and a
        # reference covering 72 tool names turns every unfamiliar tool into one of those 72.
        return [term for term in tied if term[:1] == token[:1]]

    def _choose(self, candidates: list[str], left: str, right: str) -> str:
        """Pick the candidate the surrounding words support.

        Frequency alone picks the more common word, which is wrong exactly when the query is a
        phrase: «кожы» is one edit from both «кожа» and «кожи», and only the neighbour settles
        it — the reference contains «из кожи», never «из кожа». Candidates are already sorted
        by frequency, so a context score of zero everywhere falls back to the frequent one.
        """
        bigrams = self._bigram_counts()
        if not bigrams:
            return candidates[0]

        def score(candidate: str) -> int:
            total = 0
            if left:
                total += bigrams.get(f"{left} {candidate}", 0)
            if right:
                total += bigrams.get(f"{candidate} {right}", 0)
            return total

        # `max` keeps the first of equal scores, and the list is frequency-ordered, so a tie
        # resolves exactly as it did before bigrams existed.
        return max(candidates, key=score)

    def rewrite(self, query: str) -> str | None:
        index = self._ensure_index()

        tokens = tokenize(query)
        # Corrections are applied by substitution rather than by joining tokens back together,
        # so the user's own spacing, punctuation and capitalisation survive a fix to one word
        # in the middle of a phrase.
        corrected = query
        replacements: dict[str, str] = {}

        for position, token in enumerate(tokens):
            # `is_correctable` holds the rules about *which* words may be touched — short ones,
            # function words and anything with a digit in it are left alone. They live in the
            # domain because they are the same for every stage, not a property of SymSpell.
            if token in replacements or not is_correctable(token):
                continue

            candidates = self._candidates(index, token)
            if not candidates:
                continue

            # Neighbours as *corrected so far*: a phrase with two typos in a row would
            # otherwise be scored against the misspelling next to it.
            left = tokens[position - 1] if position else ""
            right = tokens[position + 1] if position + 1 < len(tokens) else ""
            left = replacements.get(left, left)
            right = replacements.get(right, right)

            choice = self._choose(candidates, left, right)
            if choice != token:
                replacements[token] = choice

        for token, replacement in replacements.items():
            corrected = replace_token(corrected, token, replacement)

        return corrected if replacements else None
