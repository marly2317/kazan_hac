"""Сериализаторы запросов API подбора КТРУ. Ответ — готовый словарь ``Result`` (§6.3)."""

from __future__ import annotations

from rest_framework import serializers

MAX_TEXT = 2000


class AnalyzeRequestSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=MAX_TEXT, allow_blank=True, trim_whitespace=False)
    session_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PreviewRequestSerializer(serializers.Serializer):
    q = serializers.CharField(max_length=MAX_TEXT, allow_blank=True, trim_whitespace=False)


class AnswerRequestSerializer(serializers.Serializer):
    session_id = serializers.CharField()
    char_key = serializers.CharField(max_length=300)
    value = serializers.CharField(max_length=500)


class AnswerTextRequestSerializer(serializers.Serializer):
    session_id = serializers.CharField()
    char_key = serializers.CharField(max_length=300)
    text = serializers.CharField(max_length=MAX_TEXT)


class ChooseFamilyRequestSerializer(serializers.Serializer):
    session_id = serializers.CharField()
    family_id = serializers.CharField(max_length=64)


class ChooseLeafRequestSerializer(serializers.Serializer):
    session_id = serializers.CharField()
    code = serializers.CharField(max_length=64)


class ResultSerializer(serializers.Serializer):
    """Только для документации OpenAPI: поля ``Result`` (архитектура §6.3)."""

    session_id = serializers.CharField()
    stage = serializers.ChoiceField(
        choices=[
            "preview",
            "family_selected",
            "narrowing",
            "question",
            "done",
            "not_found",
            "conflict",
        ]
    )
    message = serializers.CharField()
    text = serializers.DictField()
    preview = serializers.ListField(child=serializers.DictField())
    funnel = serializers.ListField(child=serializers.DictField())
    family = serializers.DictField(allow_null=True)
    family_candidates = serializers.ListField(child=serializers.DictField())
    leaves_count = serializers.IntegerField()
    leaves = serializers.ListField(child=serializers.DictField())
    question = serializers.DictField(allow_null=True)
    final_code = serializers.CharField(allow_null=True)
    card = serializers.DictField(allow_null=True)
    to_fill_required = serializers.ListField(child=serializers.DictField())
    to_fill_optional = serializers.ListField(child=serializers.DictField())
    signals = serializers.ListField(child=serializers.DictField())
    timings_ms = serializers.DictField()
