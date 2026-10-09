"""ORM row ↔ domain entity translation."""

from __future__ import annotations

from apps.catalog.domain.entities import Product as ProductEntity
from apps.catalog.infrastructure.models import Product as ProductModel


def to_entity(row: ProductModel) -> ProductEntity:
    return ProductEntity(
        id=row.id,
        sku=row.sku,
        name=row.name,
        description=row.description,
        category=row.category,
        price=row.price,
        unit=row.unit,
        in_stock=row.in_stock,
        created_at=row.created_at,
    )
