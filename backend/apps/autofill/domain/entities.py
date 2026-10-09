"""What the analyser produces. No Django in this layer."""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.autofill.domain.enums import IssueKind, MatchState


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


@dataclass(frozen=True)
class PositionCandidate:
    code: str
    name: str
    okpd2_code: str
    score: float


@dataclass(frozen=True)
class NextStep:
    """A concrete thing the user can add to narrow things further.

    Chosen by how evenly a characteristic splits what is left, so answering it removes as many
    candidates as possible — asking about something all candidates share would be wasted.
    """

    characteristic: str
    options: list[str]
    splits_into: int


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
    def state(self) -> MatchState:
        if self.total_candidates == 1:
            return MatchState.RESOLVED
        if self.total_candidates == 0:
            return MatchState.EMPTY
        return MatchState.AMBIGUOUS

    @property
    def resolved(self) -> PositionCandidate | None:
        return self.candidates[0] if self.state is MatchState.RESOLVED else None
