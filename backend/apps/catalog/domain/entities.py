"""Catalogue entities. No Django in this layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class Product:
    id: UUID
    sku: str
    name: str
    description: str
    category: str
    price: Decimal
    unit: str
    in_stock: bool
    created_at: datetime

    @property
    def price_label(self) -> str:
        """Formatted once, here, so the API, the admin and the SPA cannot disagree."""
        return f"{self.price:,.2f} ₽".replace(",", " ")


@dataclass(frozen=True)
class SearchHit:
    """A product plus why it matched. ``rank`` orders the page; it is not shown to the user."""

    product: Product
    rank: float


@dataclass(frozen=True)
class SearchPage:
    hits: list[SearchHit]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.hits) < self.total
