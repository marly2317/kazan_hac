"""Сериализаторы подсказок."""

from __future__ import annotations

from rest_framework import serializers

from apps.autofill.application.use_cases.suggest_terms import DEFAULT_LIMIT
from apps.autofill.domain.suggest import Suggestion

#: Длиннее описания быть не может, а подсказка смотрит только на хвост.
MAX_TEXT = 1000


class SuggestRequestSerializer(serializers.Serializer):
    text = serializers.CharField(allow_blank=True, max_length=MAX_TEXT, trim_whitespace=False)
    limit = serializers.IntegerField(required=False, default=DEFAULT_LIMIT, min_value=1,
                                     max_value=20)


class SuggestionSerializer(serializers.Serializer):
    word = serializers.CharField()
    kind = serializers.CharField()
    source = serializers.CharField()

    @staticmethod
    def many_from(items: list[Suggestion]) -> list[dict]:
        return [
            {"word": item.word, "kind": item.kind.value, "source": item.source}
            for item in items
        ]
