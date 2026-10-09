"""DRF permissions shared by every module."""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request


class IsAuthenticatedUser(BasePermission):
    """Authenticated and active. Anonymous callers never reach a use case."""

    def has_permission(self, request: Request, view: Any) -> bool:
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and user.is_active)
