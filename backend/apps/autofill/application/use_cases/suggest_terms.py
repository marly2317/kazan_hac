"""Подсказки по мере набора описания."""

from __future__ import annotations

from dataclasses import dataclass

from apps.autofill.application.ports import SuggestionIndex
from apps.autofill.domain.suggest import Suggestion, rank, split_tail

#: Сколько подсказок показывать. Больше восьми — это уже не подсказка, а список, который надо
#: читать; пользователь в этот момент набирает текст, а не выбирает из каталога.
DEFAULT_LIMIT = 8


@dataclass(frozen=True)
class SuggestInput:
    text: str
    limit: int = DEFAULT_LIMIT


class SuggestTerms:
    """Два режима, и переключает их обычный пробел.

    Пока слово набирается — дополняем его. Как только пользователь поставил пробел, слово
    закончено, и полезно другое: какие слова справочник ставит следующими. Это ровно то
    поведение, которого ждут от адресной строки, и оно же решает задачу — не дать написать
    слово, которого в КТРУ нет.
    """

    def __init__(self, index: SuggestionIndex) -> None:
        self._index = index

    def execute(self, data: SuggestInput) -> list[Suggestion]:
        head, tail = split_tail(data.text)
        if tail:
            return rank(self._index.starting_with(tail, data.limit), data.limit)

        # Слово дописано: предлагаем продолжение по последнему слову законченной части.
        if not head:
            return []
        last = head.rsplit(maxsplit=1)[-1]
        return rank(self._index.following(last, data.limit), data.limit)
