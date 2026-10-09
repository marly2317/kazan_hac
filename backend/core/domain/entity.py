"""Marker base for domain entities.

Entities are frozen dataclasses: a use case that needs a changed entity asks the repository for
one, it never mutates a row in place. Nothing in this layer may import Django.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entity:
    """Identity-bearing domain object. Subclasses declare their own ``id``."""
