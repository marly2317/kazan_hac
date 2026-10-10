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

#: Shortest token worth correcting. The vocabulary itself drops terms below three characters
#: (they attract every query), so a shorter token can never be *found* in it — and a corrector
#: that cannot find a token concludes it is a typo. That is how «стул офисный **на** колесиках»
#: became «стул офисный **дна** колесиков»: `на` is two characters, so it was missing from the
#: vocabulary rather than wrong, and `дна` is one edit away and present.
MIN_CORRECTABLE_LENGTH = 3

#: Function words — never product vocabulary, never worth correcting, and dangerously short.
#: КТРУ names contain them («Обувь с верхом из кожи»), but as glue, not as terms, so they are
#: absent from the weighted vocabulary and would otherwise all look like typos.
FUNCTION_WORDS = frozenset(
    """
    без бы был была были было быть в вам вас весь во вот все всего всех вы для до его
    ее ещё еще же за и из или им их к как кто ли мне может мы на над надо наш не него
    нее нет ни них но ну об он она они оно от по под при про с со та так такой там те
    тем то того тоже той только том тот ту ты у уже чего чем что чтобы эта эти это
    этого этой этом этот эту я
    """.split()
)


def is_correctable(token: str) -> bool:
    """Whether a corrector may touch this token at all.

    Four classes of token are left exactly as typed, because every "correction" of them is
    wrong by construction:

    * **too short** — see :data:`MIN_CORRECTABLE_LENGTH`;
    * **function words** — see :data:`FUNCTION_WORDS`;
    * **anything containing a digit** — `PH2`, `M8`, `48`, `2.03`. These are article numbers,
      sizes and bit designations. `PH2` is not a misspelling of a Russian word, and an edit
      distance will happily claim otherwise;
    * **pure numbers** — `500` is not a typo for `50`.
    """
    if len(token) < MIN_CORRECTABLE_LENGTH:
        return False
    if token in FUNCTION_WORDS:
        return False
    return not any(character.isdigit() for character in token)


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


#: Characters that must agree for two words to count as forms of the same word.
#:
#: Russian inflects the ending, so «колесиках» → «колесиков» and «отвертка» → «отвертки» agree
#: on everything that carries the meaning and differ only in the grammatical tail. A real typo
#: lands inside the word instead: «кострукция» → «конструкция» already diverges at the fourth
#: character. Four is the shortest prefix that separates the two cases on the vocabulary at
#: hand, and short words are compared whole rather than by prefix.
INFLECTION_PREFIX = 4


@dataclass(frozen=True)
class TokenEdit:
    """One word the pipeline rewrote.

    Reported per word rather than per query because that is what the interface can act on: a
    notice saying «кострукция сварная стол» → «конструкция сварная стол» makes the user hunt
    for the difference, while «кострукция → конструкция» can be shown against the word itself
    and offered as a single click to undo.
    """

    before: str
    after: str

    @property
    def is_inflection(self) -> bool:
        """Whether this is the same word in another form, not a corrected mistake.

        The distinction is reported to the user, because claiming otherwise is a false
        accusation that costs trust: «колесиках» is spelled perfectly, and a service that
        announces «исправлена опечатка: колесиках → колесиков» has just told the user it found
        an error in correct input. Both rewrites are still applied — matching the reference's
        own wording helps the characteristic search — but only one of them is an error.
        """
        if min(len(self.before), len(self.after)) <= INFLECTION_PREFIX:
            return False
        return self.before[:INFLECTION_PREFIX] == self.after[:INFLECTION_PREFIX]


@dataclass(frozen=True)
class Correction:
    """What the pipeline decided about one query."""

    original: str
    corrected: str
    source: CorrectionSource

    @property
    def edits(self) -> list[TokenEdit]:
        """The individual words that changed.

        Derived by aligning the two token lists rather than reported by the stages, so every
        stage — lookup table, index, language model — gets per-word reporting without having to
        implement it. Alignment is positional, which is exact for a substitution and is why a
        rewrite that changed the *number* of words reports nothing: there is no honest pairing
        of «две ошибки» with «одна», and a wrong pairing shown against the user's own words is
        worse than no detail at all.
        """
        before, after = tokenize(self.original), tokenize(self.corrected)
        if len(before) != len(after):
            return []
        return [
            TokenEdit(before=left, after=right)
            for left, right in zip(before, after, strict=True)
            if left != right
        ]

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
