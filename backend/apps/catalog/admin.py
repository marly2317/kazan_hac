"""Django autodiscovers ``<app>.admin``; the registration lives in the presentation layer."""

from apps.catalog.presentation.admin import ProductAdmin

__all__ = ["ProductAdmin"]
