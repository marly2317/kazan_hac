"""Подстановка исправленного слова в текст пользователя. Чистые правила, без Django.

Вынесено отдельно, потому что этими правилами пользуются **обе** стадии словарного
исправления — и та, что держит индекс SymSpell в памяти, и та, что спрашивает кандидатов у
поискового движка. Различаются стадии только тем, **откуда берутся кандидаты**; что делать с
выбранным словом — вопрос один и тот же, и ответ на него должен быть один.
"""

from __future__ import annotations

import re


def match_case(sample: str, replacement: str) -> str:
    """Give ``replacement`` the capitalisation ``sample`` had.

    The vocabulary is lowercased for matching, so a correction comes back lowercase. Substituting
    it verbatim would quietly rewrite "Бумаго офисная" as "бумага офисная" in the user's own
    search box — a visible change they did not ask for on a word that was not the typo.
    """
    if sample.isupper() and len(sample) > 1:
        return replacement.upper()
    if sample[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def replace_token(text: str, token: str, replacement: str) -> str:
    """Case-insensitively replace whole occurrences of ``token``, keeping everything else.

    Подстановкой, а не сборкой текста из токенов: так правка одного слова в середине фразы
    сохраняет пробелы, знаки и регистр всего остального — пользователь видит исправленным
    ровно то слово, которое было неверным.
    """
    pattern = re.compile(rf"(?<![0-9a-zа-яё]){re.escape(token)}(?![0-9a-zа-яё])", re.IGNORECASE)
    return pattern.sub(lambda match: match_case(match.group(0), replacement), text)
