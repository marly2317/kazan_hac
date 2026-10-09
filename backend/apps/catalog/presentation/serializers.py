"""DRF serializers for the catalogue surface."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.catalog.application.use_cases.search_products import MAX_LIMIT, SearchProductsResult
from apps.catalog.domain.entities import Product
from apps.spellcheck.domain.correction import MAX_QUERY_LENGTH
from apps.spellcheck.domain.enums import CORRECTION_SOURCE_CHOICES


class ProductSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    sku = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    category = serializers.CharField(read_only=True)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    price_label = serializers.CharField(read_only=True)
    unit = serializers.CharField(read_only=True)
    in_stock = serializers.BooleanField(read_only=True)

    @staticmethod
    def from_entity(product: Product) -> dict[str, Any]:
        return {
            "id": str(product.id),
            "sku": product.sku,
            "name": product.name,
            "description": product.description,
            "category": product.category,
            "price": str(product.price),
            "price_label": product.price_label,
            "unit": product.unit,
            "in_stock": product.in_stock,
        }


class CorrectionSerializer(serializers.Serializer):
    """What the search box echoes back and what the notice under it says."""

    original = serializers.CharField(read_only=True)
    corrected = serializers.CharField(read_only=True)
    changed = serializers.BooleanField(read_only=True)
    source = serializers.ChoiceField(choices=CORRECTION_SOURCE_CHOICES, read_only=True)


class SearchQuerySerializer(serializers.Serializer):
    """Validates the query string. `q` is optional: no query lists the catalogue."""

    q = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=MAX_QUERY_LENGTH
    )
    limit = serializers.IntegerField(required=False, default=20, min_value=1, max_value=MAX_LIMIT)
    offset = serializers.IntegerField(required=False, default=0, min_value=0)
    correct = serializers.BooleanField(required=False, default=True)


class SearchResponseSerializer(serializers.Serializer):
    correction = CorrectionSerializer(read_only=True)
    results = ProductSerializer(many=True, read_only=True)
    total = serializers.IntegerField(read_only=True)
    limit = serializers.IntegerField(read_only=True)
    offset = serializers.IntegerField(read_only=True)
    has_more = serializers.BooleanField(read_only=True)

    @staticmethod
    def from_result(result: SearchProductsResult) -> dict[str, Any]:
        page = result.page
        correction = result.correction
        return {
            "correction": {
                "original": correction.original,
                "corrected": correction.corrected,
                "changed": correction.changed,
                "source": correction.source.value,
            },
            "results": [ProductSerializer.from_entity(hit.product) for hit in page.hits],
            "total": page.total,
            "limit": page.limit,
            "offset": page.offset,
            "has_more": page.has_more,
        }
