"""How closely a description's wording matches a КТРУ position's name.

This exists to answer one question: *may this candidate be shown to the user as an answer?*

Trigram similarity over the whole name — what the database does to find candidates at all — is
the wrong tool for that question, because it can only rank. It always returns a best row, and
for a product КТРУ does not contain, the best row is unrelated: «отвертки крестовая PH2 длина
150 мм» reached «Кровать детская», and nothing in a ranking says "but none of these is it".

The rule here is the one a person applies without thinking: **the thing being described has to
be named.** A position called «Молоток» answers «молоток слесарный» because the word is there.
«Кровать детская» does not answer a query about a screwdriver, whatever it scores, because no
word of the description appears in it. So the measure is the similarity of the single
best-matching *word pair*, not of the phrases — a strong match on the head noun is what
identifies a product, and it does not weaken as the user adds more detail, which is exactly the
flaw of summing over the phrase.

Pure functions, no Django, no database: the rule is part of the domain, and the thresholds are
argued for here rather than buried in a query.
"""

from __future__ import annotations

import re

#: Shortest word worth comparing. Below this, trigram overlap is dominated by the word being
#: short rather than by it being the same word.
MIN_WORD_LENGTH = 4

#: Minimum best-word similarity for a candidate to count as an answer.
#:
#: Measured against the live reference: «шкаф» ↔ «Шкаф для одежды металлический» scores 1.0,
#: «молоток» ↔ «Молоток» 1.0, «кожи» ↔ «Куртка зимняя из кожи» 1.0, while «отвертки» ↔
#: «Кровать детская» scores 0.0 and «крестовая» ↔ «Очки для защиты от радиации» 0.0. Set at
#: 0.55 so an inflected or slightly misspelled word still matches — «колесиках» ↔ «колесико»
#: is 0.6 — while an unrelated word cannot.
NAME_OVERLAP_FLOOR = 0.55

_WORD = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)


def words(text: str) -> list[str]:
    """Comparable words of a phrase: long enough to be meaningful, and not bare numbers.

    Numbers are dropped because they are values, never the identity of the product. Leaving
    «2000» in let it match the digits inside a position's name and claim the description had
    been recognised.
    """
    return [
        token
        for token in (match.group(0).casefold() for match in _WORD.finditer(text))
        if len(token) >= MIN_WORD_LENGTH and not token.isdigit()
    ]


def trigrams(word: str) -> set[str]:
    """Three-character shingles, padded so the start and end of the word are counted.

    Padding is what makes «кожа» and «кожи» differ in one trigram rather than looking like
    arbitrary substrings of each other, and it is how PostgreSQL's ``pg_trgm`` does it too — so
    the measure here and the ranking in the database agree about what similarity means.
    """
    padded = f"  {word.casefold()} "
    return {padded[index : index + 3] for index in range(len(padded) - 2)}


#: Characters compared when matching one word against another by its stem.
#:
#: Russian inflects the ending: the description says «шкаф», the characteristic is named
#: «Высота шкафа», and «высота» appears as «высоту», «высотой». A prefix of five characters
#: makes those the same word without a morphological analyser — which, for matching against a
#: fixed reference, would be a heavy dependency bought for nothing. Five is long enough that
#: «шкаф» and «шкала» stay apart.
STEM_LENGTH = 5


def stem(word: str) -> str:
    """The comparable core of a word. See :data:`STEM_LENGTH`."""
    return word.casefold()[:STEM_LENGTH]


#: Shortest shared opening that counts as the same stem.
#:
#: Three would let «шкаф» and «шкала» agree. Four keeps them apart while still catching every
#: inflection of a product name, because Russian inflects the ending and product nouns are
#: longer than four characters.
MIN_PREFIX = 4


def prefix_ratio(left: str, right: str) -> float:
    """How much of the longer word the two share from the start, in ``[0, 1]``.

    Trigram overlap alone is too strict for Russian. «молотка» and «молоток» are the same word
    — КТРУ writes «Масса головки молотка» and the user writes «молоток» — but they differ in
    two of the last three letters, which is where the trigrams are, and they score 0.455. On a
    floor of 0.55 that is an abstention on perfectly good input. The shared stem is five
    characters long and says plainly that these are one word.
    """
    shared = 0
    for one, other in zip(left, right, strict=False):
        if one != other:
            break
        shared += 1
    if shared < MIN_PREFIX:
        return 0.0
    return shared / max(len(left), len(right))


#: Короче этого слово не считается самостоятельной частью сложного слова.
#:
#: «машина» (6) внутри «шлифмашина» — часть сложного слова. «стол» (4) внутри «столешница» —
#: совпадение начала, и только: это разные вещи, и пропускать его нельзя.
MIN_COMPOUND_PART = 5


def compound_match(left: str, right: str) -> float:
    """1.0, если одно слово целиком стоит в начале или в конце другого, иначе 0.

    Русский склеивает сложные слова без дефиса: «шлиф+машина», «электро+лобзик». Для триграмм
    это разные слова — «машина» против «шлифмашина» даёт 0.385 при пороге 0.55, — и сервис
    отвечал «не нашлось» на «шлифовальная машина угловая», хотя позиция «Шлифмашина» в
    справочнике есть и поиск находит её первой же строкой.

    Просто опустить порог нельзя: «стол» против «столешница» даёт 0.40, то есть вместе с
    нужным прошло бы и ненужное. Разделяет их именно вхождение **целиком** и с краю: «машина»
    — это конец «шлифмашина», а «стол» — всего лишь первые четыре буквы «столешницы».
    """
    short, long_ = sorted((left, right), key=len)
    if len(short) < MIN_COMPOUND_PART or short == long_:
        return 0.0
    return 1.0 if long_.startswith(short) or long_.endswith(short) else 0.0


def word_similarity(left: str, right: str) -> float:
    """How likely two words are the same word, in ``[0, 1]``. Identical words score 1.

    The best of three views, because each covers the others' blind spots. Trigram overlap
    catches a letter wrong in the middle — «кострукция» against «конструкция» — but is
    defeated by an inflected ending. A shared stem catches the inflection but says nothing
    about a word mistyped near the start. A compound match catches the word standing whole
    inside another, which neither of the others sees. Taking the maximum means a word has to
    fail *all three* to be judged a different word.
    """
    if left == right:
        return 1.0
    first, second = trigrams(left), trigrams(right)
    union = first | second
    jaccard = len(first & second) / len(union) if union else 0.0
    return max(jaccard, prefix_ratio(left, right), compound_match(left, right))


def head_word(name: str) -> str:
    """The first word of a КТРУ position's name — the thing the position *is*.

    Russian product names in the reference are head-initial: «Куртка зимняя из кожи»,
    «Шкаф для одежды металлический», «Бур для перфоратора». Everything after the first word
    qualifies it. Length is not filtered here, unlike :func:`words`, because a head can be
    short — «Бур» is three characters and is precisely the word that matters.
    """
    found = _WORD.search(name)
    return found.group(0).casefold() if found else ""


def name_overlap(description: str, name: str) -> float:
    """How strongly the description names *this kind of thing*.

    Compared against the position's head word, not against any word of its name. Matching any
    word accepted «Бур для перфоратора» for «перфоратор аккумуляторный» — the word is in the
    name, but as the thing the product is *for*, not the thing it is. A drill bit is not a
    rotary hammer, and offering one as the other is the confidently-wrong answer this measure
    exists to prevent. The same rule keeps «Обувь повседневная с верхом из кожи» away from
    «куртка зимняя из кожи», where both share «кожи» and only one is a jacket.

    The maximum over the description's words, because the head noun may stand anywhere in what
    the user wrote: «зимняя куртка» names a jacket as plainly as «куртка зимняя».
    """
    head = head_word(name)
    left = words(description)
    if not head or not left:
        return 0.0
    return max(word_similarity(one, head) for one in left)


def name_coverage(description: str, name: str) -> float:
    """Share of the position name's words the description actually said, in ``[0, 1]``.

    This corrects a bias of trigram similarity: it rewards *length*. «костюм рабочий мужской»
    scored «Костюм спортивный» above «Костюм», because the longer name has more trigrams to
    overlap with a three-word query, even though the extra word — «спортивный» — is one the
    user never said and actively contradicts «рабочий». Coverage measures the opposite thing:
    how much of the position is accounted for by the description. «Костюм» is fully accounted
    for; «Костюм спортивный» is half.
    """
    left, right = words(description), words(name)
    if not right:
        return 0.0
    matched = sum(
        1
        for other in right
        if any(word_similarity(one, other) >= NAME_OVERLAP_FLOOR for one in left)
    )
    return matched / len(right)
