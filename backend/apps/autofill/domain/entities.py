"""What the analyser produces. No Django in this layer."""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.autofill.domain.enums import IssueKind, MatchState
from apps.autofill.domain.matching import NAME_OVERLAP_FLOOR


@dataclass(frozen=True)
class ExtractedCharacteristic:
    """One characteristic the analyser recognised in the description.

    Both the raw and the unified form are kept: the interface has to show *what* was unified,
    not just the result, which is one of the things the task asks to be visible.
    """

    name: str
    raw: str
    value: str
    unit: str = ""
    raw_unit: str = ""
    #: True when value or unit differ from what the user wrote.
    @property
    def was_unified(self) -> bool:
        return self.raw.casefold() != self.value.casefold() or (
            bool(self.raw_unit) and self.raw_unit.casefold() != self.unit.casefold()
        )


@dataclass(frozen=True)
class Issue:
    """A problem, with the reason attached.

    ``message`` says what is wrong and why; ``suggestions`` says what to write instead. An error
    the user cannot act on is worth nothing to them.
    """

    kind: IssueKind
    characteristic: str
    raw: str
    message: str
    suggestions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NarrowingStep:
    """One filter applied, and what it did to the candidate set.

    Recorded so the interface can show the narrowing as it happens — the task asks the user to
    be able to see how the system got from a description to a code.
    """

    label: str
    detail: str
    before: int
    after: int

    @property
    def eliminated(self) -> int:
        return max(self.before - self.after, 0)


#: What one confirmed characteristic is worth against the name-similarity score. Enough to
#: order positions with near-identical names, not enough to overturn a much better name match.
SUPPORT_BOOST = 0.35

#: What a fully-accounted-for name is worth. Roughly one word's trigram similarity, so it
#: settles a close call without overturning a clearly better name match. See
#: :func:`apps.autofill.domain.matching.name_coverage` for why it is needed at all.
COVERAGE_BOOST = 1.0


@dataclass(frozen=True)
class PositionCandidate:
    code: str
    name: str
    okpd2_code: str
    #: Name similarity from the database search — what ordered the pool initially.
    score: float
    #: How many extracted characteristics this position explicitly confirms.
    support: int = 0
    #: Best word-level match between the description and this name. See
    #: :mod:`apps.autofill.domain.matching`.
    name_overlap: float = 0.0
    #: Share of this name's words the description actually said. See
    #: :func:`apps.autofill.domain.matching.name_coverage`.
    name_coverage: float = 0.0
    #: What sets this position apart from the others still on the table, as ``«Характеристика:
    #: значение»`` lines.
    #:
    #: КТРУ routinely gives several positions the *same* name — the reference holds three
    #: positions called «Куртка зимняя из кожи» and twelve called «Молоток», differing only in
    #: their characteristics. A list of identical names is not a choice the user can make, so
    #: the characteristics that actually differ between them are carried here and shown instead.
    distinctions: list[str] = field(default_factory=list)

    @property
    def rank(self) -> float:
        """The order the user sees.

        Database similarity, plus a nudge for each characteristic the position confirms and
        for how completely the description accounts for its name. All three are *added*: no
        single signal may overturn the others on its own, which is what kept support from
        promoting a position whose only merit was declaring a characteristic the user named.
        """
        return (
            self.score
            + SUPPORT_BOOST * self.support
            + COVERAGE_BOOST * self.name_coverage
        )


@dataclass(frozen=True)
class NextStep:
    """A concrete thing the user can add to narrow things further.

    Chosen by how evenly a characteristic splits what is left, so answering it removes as many
    candidates as possible — asking about something all candidates share would be wasted.
    """

    characteristic: str
    options: list[str]
    splits_into: int
    #: Whether the answer is a number with a unit rather than one of a fixed list.
    #:
    #: The two kinds need different interfaces, and getting that wrong actively harms the user.
    #: A qualitative option («Металлический») is text that can be appended to the description
    #: verbatim, so it is offered as a button. A quantitative one is a *range* — «≥ 0.2 … < 0.4
    #: Килограмм» — which is not a value at all: appending it to the description produces text
    #: no extractor can read back. Those are shown as the permitted bounds, for the user to
    #: type a number within.
    is_quantitative: bool = False
    #: The ОКЕИ unit the answer is expected in, for a quantitative characteristic.
    unit: str = ""




@dataclass(frozen=True)
class Analysis:
    original: str
    corrected: str
    characteristics: list[ExtractedCharacteristic]
    issues: list[Issue]
    steps: list[NarrowingStep]
    candidates: list[PositionCandidate]
    next_steps: list[NextStep]
    total_candidates: int

    @property
    def text_changed(self) -> bool:
        """Whether the description the user sees differs from what was analysed.

        Case-insensitive, matching :attr:`Correction.changed`: «Сталь» analysed as «сталь» is
        the same description, and announcing «разобрано как …» for it is a notice about
        nothing. The judgement lives here rather than in the interface so there is one rule
        instead of two that can drift apart.
        """
        return self.original.casefold() != self.corrected.casefold()

    @property
    def confidence(self) -> float:
        """Whether the leading candidate is actually *named* by the description, in ``[0, 1]``.

        Deliberately not the database's ranking score. That score is a sum over the
        description's words, so it grows with how much the user typed and shrinks when they
        type little — an absolute threshold on it called «шкаф офисный 2000 мм высота»
        unmatched (0.61) while accepting a weaker match on a longer query. Word-level overlap
        does not have that defect: it measures whether the product noun is there, and adding
        detail cannot lower it.
        """
        return self.candidates[0].name_overlap if self.candidates else 0.0

    @property
    def state(self) -> MatchState:
        """How far the narrowing got — and whether the result may be shown as an answer.

        The confidence check comes before the count check, so a single surviving candidate that
        nothing in the description actually resembles reports ``NOT_FOUND`` rather than being
        promoted to ``RESOLVED`` by virtue of being alone. Being the only candidate left is not
        evidence of being the right one.
        """
        if not self.candidates:
            return MatchState.EMPTY
        if self.confidence < NAME_OVERLAP_FLOOR:
            return MatchState.NOT_FOUND
        if self.total_candidates == 1:
            return MatchState.RESOLVED
        return MatchState.AMBIGUOUS

    @property
    def resolved(self) -> PositionCandidate | None:
        return self.candidates[0] if self.state is MatchState.RESOLVED else None
