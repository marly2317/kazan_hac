"""The CSRF failure view.

``csrf_protect`` rejects a request *before* the view runs, so DRF's exception handler never sees
it and Django answers with its own HTML page. For an API caller that is a response it cannot
parse: the SPA expects one envelope for every error. This renders that envelope instead.

Anything outside ``/api/`` — the admin, most notably — keeps Django's HTML page, which is the
right answer for a browser form.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.csrf import csrf_failure as django_csrf_failure

from core.presentation.exception_handler import envelope

MESSAGE = "Истёк токен безопасности. Обновите страницу и попробуйте ещё раз."


def csrf_failure(request: HttpRequest, reason: str = "", **kwargs) -> HttpResponse:
    if not request.path.startswith("/api/"):
        return django_csrf_failure(request, reason=reason, **kwargs)
    # `reason` names the specific check that failed and is deliberately not forwarded: it is
    # diagnostic detail for the log, not for the client.
    return JsonResponse(envelope("csrf_failed", MESSAGE), status=403)
