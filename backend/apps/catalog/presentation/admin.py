"""Back-office for the catalogue."""

from __future__ import annotations

from django.contrib import admin

from apps.catalog.infrastructure.models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "category", "price", "unit", "in_stock")
    list_filter = ("category", "in_stock")
    search_fields = ("sku", "name", "description")
    readonly_fields = ("id", "created_at")

    def save_model(self, request, obj, form, change) -> None:
        super().save_model(request, obj, form, change)
        # The stored vector and the spelling vocabulary are both derived from these columns, so
        # an edit here has to invalidate them or search keeps answering with the old text.
        from config import container

        container.product_repository().refresh_search_vectors()
        container.reset_spelling_indexes()
