"""Django looks for ``<app>.models``; the real models live in the infrastructure layer."""

from apps.ktru.infrastructure.models import Characteristic, PermittedValue, Position

__all__ = ["Characteristic", "PermittedValue", "Position"]
