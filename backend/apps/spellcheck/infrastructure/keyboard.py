"""Wrong-keyboard-layout recovery.

Someone types a Russian query without noticing the layout is still English and gets ``ghbdtn``
instead of ``привет``. It is the single most common "typo" in Russian-language search and it is
not a typo at all: the keystrokes were right, so the fix is a character-for-character
substitution with no edit distance involved.

Open-source Russian search stacks all carry some form of this table; it is reproduced here
rather than pulled in as a dependency because it is forty lines and never changes.
"""

from __future__ import annotations

from apps.spellcheck.application.ports import VocabularySource
from apps.spellcheck.domain.correction import is_correctable, tokenize

# ЙЦУКЕН ↔ QWERTY, by physical key.
#
# Letters and punctuation are built separately on purpose. A punctuation key has no distinct
# uppercase form — `","` upper-cases to itself — so folding both cases into one `maketrans`
# call lets the uppercase pass overwrite `,` with `Б` and every comma-key slip comes back
# capitalised mid-word.
_EN_LETTERS = "qwertyuiopasdfghjklzxcvbnm"
_RU_LETTERS = "йцукенгшщзфывапролдячсмить"

#: The remaining physical keys. `/` and `.` genuinely swap places between the two layouts.
_EN_PUNCT = "[];',.`/"
_RU_PUNCT = "хъжэбюё."

assert len(_EN_LETTERS) == len(_RU_LETTERS)
assert len(_EN_PUNCT) == len(_RU_PUNCT)

_EN_TO_RU = str.maketrans(
    _EN_LETTERS + _EN_LETTERS.upper() + _EN_PUNCT,
    _RU_LETTERS + _RU_LETTERS.upper() + _RU_PUNCT,
)
_RU_TO_EN = str.maketrans(
    _RU_LETTERS + _RU_LETTERS.upper() + _RU_PUNCT,
    _EN_LETTERS + _EN_LETTERS.upper() + _EN_PUNCT,
)


def to_russian(text: str) -> str:
    return text.translate(_EN_TO_RU)


def to_latin(text: str) -> str:
    return text.translate(_RU_TO_EN)


class KeyboardLayoutRewriter:
    """Flips the layout when doing so turns gibberish into words the catalogue knows.

    The vocabulary check is what keeps this stage safe. Without it, ``asus`` becomes ``фыгы``
    and a perfectly good Latin brand name is destroyed. A flip is only accepted when it strictly
    increases the number of tokens the catalogue recognizes.
    """

    def __init__(self, vocabulary: VocabularySource) -> None:
        self._vocabulary = vocabulary
        self._known: frozenset[str] | None = None

    def _known_terms(self) -> frozenset[str]:
        if self._known is None:
            self._known = frozenset(word for word, _ in self._vocabulary.terms())
        return self._known

    def _recognized(self, text: str) -> int:
        known = self._known_terms()
        return sum(1 for token in _words(text) if token in known)

    def rewrite(self, query: str) -> str | None:
        words = _words(query)
        current = self._recognized(query)
        # A query the catalogue already understands is not a layout accident, whatever it looks
        # like. Checking this first keeps the stage off correct queries entirely.
        #
        # Counted over `_words` rather than every token: prepositions and article numbers are
        # never in the vocabulary, so counting them made the test "are all words recognised?"
        # impossible to pass for «стул офисный на колесиках» and left the stage running on
        # queries it had already judged correct.
        if current and current == len(words):
            return None

        for flipped in (to_russian(query), to_latin(query)):
            if flipped != query and self._recognized(flipped) > current:
                return flipped
        return None


def _words(text: str) -> list[str]:
    """Tokens that could meaningfully be in the vocabulary — the rest cannot be judged."""
    return [token for token in tokenize(text) if is_correctable(token)]
