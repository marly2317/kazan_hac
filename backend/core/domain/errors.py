"""Domain error hierarchy.

Every module raises subclasses of these four. The DRF exception handler maps them to status
codes, so no use case ever names an HTTP status itself. ``code`` is the stable machine-readable
slug the SPA branches on; ``message`` is the text shown to the user.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    code: str = "domain_error"
    message: str = "Не удалось выполнить операцию."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = details or {}
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ValidationError(DomainError):
    code = "validation_error"
    message = "Переданы некорректные данные."


class AccessDeniedError(DomainError):
    code = "access_denied"
    message = "Доступ запрещён."


class NotFoundError(DomainError):
    code = "not_found"
    message = "Объект не найден."


class ConflictError(DomainError):
    code = "conflict"
    message = "Действие конфликтует с текущим состоянием."
