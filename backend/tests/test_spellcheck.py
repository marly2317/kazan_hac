"""The correction pipeline. The domain and pipeline tests need no database."""

from __future__ import annotations

import pytest

from apps.spellcheck.application.use_cases.correct_query import CorrectQuery
from apps.spellcheck.domain.correction import (
    Correction,
    normalize_query,
    tokenize,
)
from apps.spellcheck.domain.enums import CorrectionSource
from apps.spellcheck.domain.exceptions import QueryTooLong
from apps.spellcheck.infrastructure.keyboard import (
    KeyboardLayoutRewriter,
    to_latin,
    to_russian,
)
from apps.spellcheck.infrastructure.llm import _clean, _plausible
from apps.spellcheck.infrastructure.symspell_index import SymSpellRewriter


class FakeVocabulary:
    """A vocabulary of equally frequent words, with optional word-pair context."""

    def __init__(self, *words: str, pairs: dict[str, int] | None = None) -> None:
        self._words = words
        self._pairs = pairs or {}

    def terms(self):
        return [(word, 10) for word in self._words]

    def bigrams(self):
        return list(self._pairs.items())


# --- domain ---------------------------------------------------------------------------------


def test_normalize_collapses_whitespace_and_nbsp() -> None:
    assert normalize_query("  бумага   А4 ") == "бумага А4"


def test_normalize_rejects_an_overlong_query() -> None:
    with pytest.raises(QueryTooLong):
        normalize_query("а" * 201)


def test_tokenize_keeps_article_numbers_whole() -> None:
    assert tokenize("скобы №24/6 для степлера") == ["скобы", "24/6", "для", "степлера"]


def test_changed_ignores_a_case_only_difference() -> None:
    assert not Correction("Ноутбук", "ноутбук", CorrectionSource.DICTIONARY).changed
    assert Correction("наутбук", "ноутбук", CorrectionSource.DICTIONARY).changed


def test_then_keeps_the_originally_typed_text() -> None:
    first = Correction.unchanged("abc").then("бис", CorrectionSource.LAYOUT)
    second = first.then("бас", CorrectionSource.DICTIONARY)

    # Not "бис": the notice compares against what the user typed, not an intermediate string.
    assert second.original == "abc"
    assert second.corrected == "бас"
    assert second.source is CorrectionSource.DICTIONARY


# --- keyboard layout ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word", ["ноутбук", "картридж", "кресло", "бумага", "степлер", "Ноутбук", "мышь"]
)
def test_layout_table_round_trips(word: str) -> None:
    assert to_russian(to_latin(word)) == word


def test_layout_does_not_uppercase_punctuation_keys() -> None:
    # `,` and `.` have no distinct uppercase, so a shared maketrans would turn them into `Б`/`Ю`.
    assert to_russian("yjen,er") == "ноутбук"
    assert to_russian("Yjen,er") == "Ноутбук"


def test_layout_rewriter_flips_when_it_yields_known_words() -> None:
    rewriter = KeyboardLayoutRewriter(FakeVocabulary("ноутбук", "картридж"))

    assert rewriter.rewrite("yjen,er") == "ноутбук"


def test_layout_rewriter_leaves_a_known_query_alone() -> None:
    rewriter = KeyboardLayoutRewriter(FakeVocabulary("ноутбук"))

    assert rewriter.rewrite("ноутбук") is None


def test_layout_rewriter_does_not_mangle_a_latin_brand() -> None:
    # Flipping `asus` gives `фыгы`, which is not a word — the vocabulary check must refuse it.
    rewriter = KeyboardLayoutRewriter(FakeVocabulary("ноутбук", "монитор"))

    assert rewriter.rewrite("asus") is None


# --- dictionary (SymSpell) --------------------------------------------------------------------


def test_symspell_fixes_a_typo_towards_the_catalogue() -> None:
    rewriter = SymSpellRewriter(FakeVocabulary("ноутбук", "монитор", "картридж"))

    assert rewriter.rewrite("наутбук") == "ноутбук"


def test_symspell_leaves_a_correct_query_alone() -> None:
    rewriter = SymSpellRewriter(FakeVocabulary("ноутбук"))

    assert rewriter.rewrite("ноутбук") is None


def test_symspell_preserves_surrounding_text_and_case() -> None:
    rewriter = SymSpellRewriter(FakeVocabulary("бумага", "офисная"))

    assert rewriter.rewrite("Бумаго офисная, А4") == "Бумага офисная, А4"


def test_symspell_uses_context_to_choose_between_equal_candidates() -> None:
    # "кожы" is one edit from both "кожа" and "кожи"; only the neighbour settles it.
    vocabulary = FakeVocabulary("кожа", "кожи", "куртка", "из", pairs={"из кожи": 40})

    assert SymSpellRewriter(vocabulary).rewrite("куртка из кожы") == "куртка из кожи"


def test_symspell_falls_back_to_frequency_without_context() -> None:
    vocabulary = FakeVocabulary("кожа", "кожи", "из")

    # No bigrams: whichever SymSpell ranks first, but it must still be one of the two and the
    # call must not fail for want of context.
    assert SymSpellRewriter(vocabulary).rewrite("из кожы") in {"из кожа", "из кожи"}


def test_symspell_never_touches_a_number() -> None:
    rewriter = SymSpellRewriter(FakeVocabulary("бумага", "1000"))

    assert rewriter.rewrite("бумага 500") is None


# --- the pipeline -----------------------------------------------------------------------------


class StaticRewriter:
    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.calls = 0

    def rewrite(self, query: str) -> str | None:
        self.calls += 1
        return self.answer


class ExplodingRewriter:
    def rewrite(self, query: str) -> str | None:
        raise RuntimeError("the model is down")


def test_the_first_effective_stage_wins_and_later_ones_do_not_run() -> None:
    second = StaticRewriter("из словаря")
    use_case = CorrectQuery(
        [
            (CorrectionSource.LAYOUT, StaticRewriter("из раскладки")),
            (CorrectionSource.DICTIONARY, second),
        ]
    )

    result = use_case.execute("исходник")

    assert result.corrected == "из раскладки"
    assert result.source is CorrectionSource.LAYOUT
    assert second.calls == 0


def test_a_failing_stage_is_skipped_rather_than_failing_the_search() -> None:
    use_case = CorrectQuery(
        [
            (CorrectionSource.LLM, ExplodingRewriter()),
            (CorrectionSource.DICTIONARY, StaticRewriter("исправлено")),
        ]
    )

    result = use_case.execute("исходник")

    assert result.corrected == "исправлено"
    assert result.source is CorrectionSource.DICTIONARY


def test_every_stage_failing_returns_the_query_untouched() -> None:
    result = CorrectQuery([(CorrectionSource.LLM, ExplodingRewriter())]).execute("запрос")

    assert result.corrected == "запрос"
    assert not result.changed
    assert result.source is CorrectionSource.NONE


def test_an_empty_query_skips_the_pipeline() -> None:
    stage = StaticRewriter("что-то")
    result = CorrectQuery([(CorrectionSource.LAYOUT, stage)]).execute("   ")

    assert result.corrected == ""
    assert stage.calls == 0


# --- the language-model guard -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"ноутбук"', "ноутбук"),
        ("«ноутбук»", "ноутбук"),
        ("  ноутбук.  ", "ноутбук"),
        ("ноутбук\n", "ноутбук"),
    ],
)
def test_clean_strips_the_wrapping_a_chat_model_adds(raw: str, expected: str) -> None:
    assert _clean(raw) == expected


@pytest.mark.parametrize("answer", ["ноутбук", "фотобумага 4", "бумага а4"])
def test_plausible_accepts_a_real_correction(answer: str) -> None:
    assert _plausible("фотобумага4", answer)


@pytest.mark.parametrize(
    "answer",
    [
        "",
        "Конечно! Вот исправленный запрос, который вы просили, а также несколько советов "
        "по выбору подходящего ноутбука для офисной работы в 2026 году.",
        "строка\nещё строка",
    ],
)
def test_plausible_rejects_a_model_that_answered_instead_of_correcting(answer: str) -> None:
    assert not _plausible("наутбук", answer)
