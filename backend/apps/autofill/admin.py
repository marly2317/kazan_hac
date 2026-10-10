"""Django autodiscovers ``<app>.admin``; the registration lives in the presentation layer."""

from apps.autofill.presentation.admin import ReferenceGapAdmin

__all__ = ["ReferenceGapAdmin"]
