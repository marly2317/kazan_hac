"""ORM models for the catalogue. The domain entity, not this model, leaves the module."""

from __future__ import annotations

import uuid

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models


class Product(models.Model):
    """A catalogue item.

    ``search_vector`` is maintained by the repository on write rather than computed per query:
    ``to_tsvector`` over every row on every search is the usual reason a search box gets slow
    at a few thousand products, and a stored vector is what a GIN index can actually index.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sku = models.CharField(max_length=64, unique=True, verbose_name="артикул")
    name = models.CharField(max_length=300, verbose_name="наименование")
    description = models.TextField(blank=True, verbose_name="описание")
    category = models.CharField(max_length=120, db_index=True, verbose_name="категория")
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="цена")
    unit = models.CharField(max_length=32, default="шт", verbose_name="единица")
    in_stock = models.BooleanField(default=True, db_index=True, verbose_name="в наличии")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="добавлен")

    search_vector = SearchVectorField(null=True, editable=False)

    class Meta:
        app_label = "catalog"
        db_table = "catalog_product"
        ordering = ["name"]
        verbose_name = "товар"
        verbose_name_plural = "товары"
        indexes = [
            # Full-text ranking.
            GinIndex(fields=["search_vector"], name="catalog_product_fts"),
            # Trigram similarity, for the fuzzy half of the query. Without this index every
            # `%` comparison is a sequential scan over the whole table.
            GinIndex(
                name="catalog_product_trgm",
                fields=["name"],
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sku} — {self.name}"
