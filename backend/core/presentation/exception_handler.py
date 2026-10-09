"""The single DRF exception handler.

Every error the API returns — domain, DRF or unexpected — leaves through here in one envelope:

    {"error": {"code": "invalid_credentials", "message": "…", "details": {}}}

The SPA parses exactly one shape. New modules must not invent their own.
"""

from __future__ import annotations

import logging
from typing import Any

from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from core.domain.errors import (
    AccessDeniedError,
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

#: Most specific first, so a module error deriving from ``ConflictError`` keeps 409 even though
#: it also derives from ``DomainError``.
DOMAIN_STATUS_MAP: list[tuple[type[DomainError], int]] = [
    (ValidationError, status.HTTP_400_BAD_REQUEST),
    (AccessDeniedError, status.HTTP_403_FORBIDDEN),
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ConflictError, status.HTTP_409_CONFLICT),
]

GENERIC_SERVER_ERROR = {
    "code": "internal_error",
    "message": "Внутренняя ошибка сервера. Попробуйте позже.",
    "details": {},
}


def status_for(error: DomainError) -> int:
    """Map a domain error to its documented status code.

    A domain error that is not one of the four base kinds is a programming mistake, and 500 is
    the honest answer.
    """
    for base, code in DOMAIN_STATUS_MAP:
        if isinstance(error, base):
            return code
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _drf_details(detail: Any) -> tuple[str, dict[str, Any]]:
    """Turn a DRF detail (string, list or nested dict) into a message plus structured details."""
    if isinstance(detail, dict):
        return "Переданы некорректные данные.", dict(detail)
    if isinstance(detail, list):
        return (str(detail[0]) if detail else "Переданы некорректные данные."), {"errors": detail}
    return str(detail), {}


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    if isinstance(exc, DomainError):
        http_status = status_for(exc)
        if http_status >= 500:
            logger.exception("Unmapped domain error", exc_info=exc)
            return Response(envelope(**GENERIC_SERVER_ERROR), status=http_status)
        body = exc.as_dict()
        return Response(
            envelope(body["code"], body["message"], body["details"]), status=http_status
        )

    if isinstance(exc, Http404):
        return Response(envelope("not_found", NotFoundError.message), status=404)

    if isinstance(exc, drf_exceptions.APIException):
        response = drf_exception_handler(exc, context)
        message, details = _drf_details(exc.detail)
        code = getattr(exc, "default_code", "error")
        http_status = response.status_code if response is not None else exc.status_code
        reshaped = Response(envelope(str(code), message, details), status=http_status)
        if response is not None:
            for header, value in response.headers.items():
                reshaped[header] = value
        return reshaped

    # Anything unhandled: the traceback goes to the log, never to the client.
    logger.exception("Unhandled exception in API view", exc_info=exc)
    return Response(envelope(**GENERIC_SERVER_ERROR), status=500)
