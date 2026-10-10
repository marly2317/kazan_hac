"""Подсказки по мере набора. Чистая логика — ни базы, ни Django."""

from __future__ import annotations

from apps.autofill.application.use_cases.suggest_terms import SuggestInput, SuggestTerms
from apps.autofill.domain.suggest import Suggestion, SuggestionKind, rank, same_word, split_tail


def suggestion(
    word: str, kind=SuggestionKind.VALUE, frequency: int = 1, head: bool = False
) -> Suggestion:
    return Suggestion(
        word=word, kind=kind, source=f"фраза про {word}", frequency=frequency, is_head=head
    )


class FakeIndex:
    def __init__(self, words: list[Suggestion], nxt: list[Suggestion] | None = None) -> None:
        self._words = words
        self._next = nxt or []
        self.asked_prefix: str | None = None
        self.asked_following: str | None = None

    def starting_with(self, prefix: str, limit: int) -> list[Suggestion]:
        self.asked_prefix = prefix
        return [s for s in self._words if s.word.startswith(prefix.casefold())]

    def following(self, word: str, limit: int) -> list[Suggestion]:
        self.asked_following = word
        return self._next


# --- где кончается набранное слово ------------------------------------------------------


def test_a_trailing_space_means_the_word_is_finished() -> None:
    # Это и переключает подсказку с дополнения слова на предложение следующего.
    assert split_tail("куртка ") == ("куртка", "")
    assert split_tail("куртка зим") == ("куртка", "зим")
    assert split_tail("кур") == ("", "кур")
    assert split_tail("") == ("", "")


# --- формы одного слова -----------------------------------------------------------------


def test_inflected_forms_count_as_one_word() -> None:
    # Четыре формы «металлический» вытеснили бы из списка настоящие варианты.
    assert same_word("металлический", "металлическим")
    assert same_word("письменный", "письменного")
    assert same_word("отверстия", "отверстий")


def test_different_words_with_a_shared_start_do_not_merge() -> None:
    # «отверстие» и «отвёртка» начинаются одинаково и означают разное.
    assert not same_word("отверстия", "отвертки")
    assert not same_word("металлический", "металлизированная")
    assert not same_word("шлифмашина", "шлифовальная")


def test_ranking_keeps_one_form_per_word() -> None:
    found = rank(
        [
            suggestion("металлическим", frequency=1),
            suggestion("металлический", frequency=9),
            suggestion("металлизированная", frequency=1),
        ],
        limit=8,
    )

    # Остаётся самая частая форма — и отдельным словом то, что формой не является.
    assert [s.word for s in found] == ["металлический", "металлизированная"]


def test_a_word_from_a_position_name_outranks_a_value() -> None:
    # Цель набора — дойти до кода КТРУ, поэтому слово, ведущее к позиции, важнее.
    found = rank(
        [
            suggestion("стальной", SuggestionKind.VALUE, frequency=99),
            suggestion("стол", SuggestionKind.POSITION, frequency=1),
        ],
        limit=8,
    )

    assert [s.word for s in found] == ["стол", "стальной"]


# --- сценарий ----------------------------------------------------------------------------


def test_an_unfinished_word_is_completed() -> None:
    index = FakeIndex([suggestion("куртка", SuggestionKind.POSITION), suggestion("курвиметр")])

    found = SuggestTerms(index).execute(SuggestInput(text="зимняя кур"))

    assert index.asked_prefix == "кур"
    assert [s.word for s in found] == ["куртка", "курвиметр"]


def test_after_a_space_the_next_word_is_offered() -> None:
    # «Подскажи следующее слово»: что справочник ставит после «куртка».
    index = FakeIndex([], nxt=[suggestion("зимняя"), suggestion("мужская")])

    found = SuggestTerms(index).execute(SuggestInput(text="куртка "))

    assert index.asked_following == "куртка"
    assert [s.word for s in found] == ["зимняя", "мужская"]


def test_an_empty_box_suggests_nothing() -> None:
    # Список подсказок над пустым полем — это не подсказка, а каталог.
    index = FakeIndex([suggestion("куртка")])

    assert SuggestTerms(index).execute(SuggestInput(text="")) == []


def test_the_limit_is_respected() -> None:
    index = FakeIndex([suggestion(f"кура{i}" * 2) for i in range(20)])

    assert len(SuggestTerms(index).execute(SuggestInput(text="кура", limit=3))) == 3


def test_the_product_noun_outranks_a_qualifier_from_the_same_name() -> None:
    # Наименования позиций описательны, и без этого правила каждое прилагательное внутри них
    # получало высший вес наравне с существительным: на «по» первым шло «пониженных» — из
    # «Обувь специальная для защиты от пониженных температур», — а не «полупальто».
    found = rank(
        [
            suggestion("пониженных", SuggestionKind.POSITION, frequency=40),
            suggestion("полупальто", SuggestionKind.POSITION, frequency=1, head=True),
        ],
        limit=8,
    )

    assert [s.word for s in found] == ["полупальто", "пониженных"]
