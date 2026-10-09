"""Django looks for ``<app>.models``; the real models live in the infrastructure layer."""

from apps.catalog.infrastructure.models import Product

__all__ = ["Product"]
