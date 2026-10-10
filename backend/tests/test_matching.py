"""Whether a candidate may be shown as an answer. Pure functions — no database, no Django.

Every case here is a real failure from the live reference. The measures in
`apps.autofill.domain.matching` exist because trigram ranking cannot say "none of these is
it": it always returns a best row, and for a product КТРУ does not contain, that row is
unrelated and was being presented as the answer.
"""

from __future__ import annotations

from apps.autofill.domain.matching import (
    NAME_OVERLAP_FLOOR,
    head_word,
    name_coverage,
    name_overlap,
    word_similarity,
    words,
)

# --- which words count ----------------------------------------------------------------------


def test_numbers_are_not_words() -> None:
    # A value is never the identity of a product. Leaving «2000» in let it match digits inside
    # a position name and claim the description had been recognised.
    assert words("шкаф офисный 2000 мм высота") == ["шкаф", "офисный", "высота"]


def test_short_words_are_dropped() -> None:
    # Below four characters trigram overlap measures shortness, not sameness.
    assert words("стол из ДСП для офиса") == ["стол", "офиса"]


# --- the head word --------------------------------------------------------------------------


def test_the_head_word_is_the_first_one_however_short() -> None:
    # «Бур» is three characters and is exactly the word that decides the match.
    assert head_word("Бур для перфоратора") == "бур"
    assert head_word("Куртка зимняя из кожи") == "куртка"


def test_a_product_named_only_in_a_qualifier_does_not_match() -> None:
    # «Бур для перфоратора» contains «перфоратора», but as the thing the product is *for*.
    # A drill bit is not a rotary hammer, and КТРУ has no rotary hammer to offer instead.
    assert name_overlap("перфоратор аккумуляторный SDS-plus", "Бур для перфоратора") == 0.0


def test_a_shared_qualifier_does_not_make_a_match() -> None:
    # Both names end in «из кожи»; only one is a jacket.
    jacket = name_overlap("куртка зимняя из кожи", "Куртка зимняя из кожи")
    boots = name_overlap("куртка зимняя из кожи", "Обувь повседневная с верхом из кожи")

    assert jacket >= NAME_OVERLAP_FLOOR
    assert boots < NAME_OVERLAP_FLOOR


def test_the_head_noun_may_stand_anywhere_in_the_description() -> None:
    # «зимняя куртка» names a jacket as plainly as «куртка зимняя».
    assert name_overlap("зимняя куртка из кожи", "Куртка зимняя из кожи") == 1.0


def test_an_inflected_head_still_matches() -> None:
    # The floor has to clear ordinary Russian inflection, or the service abstains on valid
    # input. «рукоятка молотка» is how the genitive is written, and КТРУ's own characteristic
    # is named «Масса головки молотка».
    assert name_overlap("рукоятка молотка", "Молоток") >= NAME_OVERLAP_FLOOR
    assert name_overlap("шкафы металлические", "Шкаф для одежды металлический") >= (
        NAME_OVERLAP_FLOOR
    )


def test_a_mistyped_head_is_the_correctors_job_not_this_measure() -> None:
    # «малоток» is two letters from «молоток» in the middle of the word and scores below the
    # floor on its own. That is correct: the spelling pipeline runs *before* matching and
    # hands this measure «молоток». Lowering the floor until raw typos passed would let
    # unrelated words through too.
    assert name_overlap("малоток слесарный", "Молоток") < NAME_OVERLAP_FLOOR
    assert name_overlap("молоток слесарный", "Молоток") == 1.0


def test_a_shared_stem_is_not_enough_on_its_own() -> None:
    # Four characters of agreement, not three: «шкаф» and «шкала» must stay apart.
    assert word_similarity("шкаф", "шкала") < NAME_OVERLAP_FLOOR
    assert word_similarity("стол", "столешница") < NAME_OVERLAP_FLOOR


# --- coverage -------------------------------------------------------------------------------


def test_coverage_prefers_the_name_the_description_accounts_for() -> None:
    # Trigram similarity rewards length: «Костюм спортивный» outscored «Костюм» for «костюм
    # рабочий мужской», on the strength of a word the user never said and which contradicts
    # one they did. Coverage measures the opposite thing.
    assert name_coverage("костюм рабочий мужской", "Костюм") == 1.0
    assert name_coverage("костюм рабочий мужской", "Костюм спортивный") == 0.5


def test_coverage_of_an_unrelated_name_is_low() -> None:
    assert name_coverage("куртка зимняя", "Обувь повседневная с верхом из кожи") == 0.0


# --- the similarity measure itself ------------------------------------------------------------


def test_identical_words_score_one() -> None:
    assert word_similarity("молоток", "молоток") == 1.0


def test_unrelated_words_score_zero() -> None:
    assert word_similarity("отвертки", "кровать") == 0.0
