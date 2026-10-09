"""Shared OpenAPI building blocks.

Every module documents the *same* error envelope, so the shape is declared once here and
referenced from the view annotations. A second, slightly different copy is how a generated
client starts disagreeing with the server.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiResponse
from rest_framework import serializers

#: Human-readable description per status code the API can answer with.
_DESCRIPTIONS = {
    400: "Некорректные данные или неверные учётные данные.",
    403: "Доступ запрещён или сессия отсутствует.",
    404: "Объект не найден.",
    409: "Конфликт с текущим состоянием.",
}


class ErrorBodySerializer(serializers.Serializer):
    code = serializers.CharField(help_text="Стабильный машинный код ошибки.")
    message = serializers.CharField(help_text="Текст для показа пользователю.")
    details = serializers.DictField(required=False)


class ErrorEnvelopeSerializer(serializers.Serializer):
    """Documentation of what the exception handler already returns — never used to build one."""

    error = ErrorBodySerializer()


def error_responses(*codes: int) -> dict[int, Any]:
    return {
        code: OpenApiResponse(
            response=ErrorEnvelopeSerializer,
            description=_DESCRIPTIONS.get(code, "Ошибка."),
        )
        for code in codes
    }
