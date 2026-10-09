"""Where a correction came from. Shown to the user and logged for tuning the pipeline."""

from enum import StrEnum


class CorrectionSource(StrEnum):
    #: Nothing was changed.
    NONE = "none"
    #: The query was typed with the keyboard in the wrong layout (``ghbdtn`` → ``привет``).
    LAYOUT = "layout"
    #: Fixed against the vocabulary of the catalogue itself.
    DICTIONARY = "dictionary"
    #: Rewritten by the language model.
    LLM = "llm"


CORRECTION_SOURCE_CHOICES = [(item.value, item.value) for item in CorrectionSource]
