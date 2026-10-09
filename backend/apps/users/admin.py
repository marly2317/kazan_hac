"""Django autodiscovers ``<app>.admin``; the registration lives in the presentation layer."""

from apps.users.presentation.admin import UserAdmin

__all__ = ["UserAdmin"]
