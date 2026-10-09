"""Spellcheck errors."""

from core.domain.errors import ValidationError


class QueryTooLong(ValidationError):
    code = "query_too_long"
    message = "Поисковый запрос слишком длинный."
