"""Kinds of problem the analyser reports, and how confident a match is."""

from enum import StrEnum


class IssueKind(StrEnum):
    """Why a piece of the description is wrong.

    The task asks for errors to be reported "с обоснованием", so each kind carries its own
    explanation template — a user told only "ошибка" cannot act on it.
    """

    #: Fixed by the spelling pipeline before matching.
    TYPO = "typo"
    #: The same word brought to the form the reference uses — not a mistake.
    NORMALIZED = "normalized"
    #: The value is not among those КТРУ permits for this characteristic.
    UNKNOWN_VALUE = "unknown_value"
    #: A number outside every permitted range.
    OUT_OF_RANGE = "out_of_range"
    #: A unit КТРУ does not use for this characteristic.
    WRONG_UNIT = "wrong_unit"
    #: Not a unit of measure at all.
    UNKNOWN_UNIT = "unknown_unit"
    #: A required characteristic nothing in the description supplies.
    MISSING_REQUIRED = "missing_required"
    #: The description resembles nothing in the reference well enough to answer.
    NO_MATCH = "no_match"


ISSUE_KIND_CHOICES = [(kind.value, kind.value) for kind in IssueKind]


class MatchState(StrEnum):
    """How far the narrowing got."""

    #: Exactly one position left — the goal.
    RESOLVED = "resolved"
    #: Several positions still fit; the user is told what to add.
    AMBIGUOUS = "ambiguous"
    #: Nothing matched at all.
    EMPTY = "empty"
    #: Something matched, but too weakly to put a code in front of the user.
    #:
    #: Distinct from ``EMPTY`` on purpose. «Отвертка» is absent from КТРУ — across all 183
    #: archives there is no position whose name contains «отвертк» outside the medical group —
    #: yet trigram similarity always returns *something*, and «отвертки ростовая» reached
    #: «Очки для защиты от радиации» at a score of 0.51 and was reported as ``RESOLVED``. A
    #: confidently wrong code is the worst answer this service can give: the user has no reason
    #: to doubt it. This state is the honest one, and it carries the near misses with it.
    NOT_FOUND = "not_found"
