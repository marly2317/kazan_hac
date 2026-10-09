"""Shared repository plumbing."""

from __future__ import annotations

from typing import Any, TypeVar

from django.db.models import Model, QuerySet

from core.domain.errors import NotFoundError

TModel = TypeVar("TModel", bound=Model)


class Repository:
    """Base for ORM-backed repositories.

    Repositories return domain entities, never ORM rows: the mapper is what keeps a Django model
    from leaking past this layer. ``not_found_error`` lets each module raise its own 404 while
    reusing ``get_or_raise``.
    """

    not_found_error: type[NotFoundError] = NotFoundError

    def get_or_raise(self, queryset: QuerySet[TModel], **lookup: Any) -> TModel:
        row = queryset.filter(**lookup).first()
        if row is None:
            raise self.not_found_error()
        return row
