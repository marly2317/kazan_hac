"""Catalogue errors."""

from core.domain.errors import NotFoundError, ValidationError


class ProductNotFound(NotFoundError):
    code = "product_not_found"
    message = "Товар не найден."


class InvalidPageSize(ValidationError):
    code = "invalid_page_size"
    message = "Некорректный размер страницы."
