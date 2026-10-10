"""Django expects ``<app>.models``; the real models live in the infrastructure layer."""

from apps.autofill.infrastructure.models import ReferenceGap

__all__ = ["ReferenceGap"]
