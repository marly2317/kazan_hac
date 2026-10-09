"""ORM models for the КТРУ reference.

The shape mirrors the XML: a position has characteristics, a characteristic has permitted
values. Flattening it would lose exactly what the task needs — which values are allowed for
which characteristic of which position.
"""

from __future__ import annotations

from django.contrib.postgres.indexes import GinIndex
from django.db import models


class Position(models.Model):
    """A КТРУ position. ``code`` is the natural key and the thing the user is matched to."""

    code = models.CharField(max_length=64, primary_key=True, verbose_name="код КТРУ")
    name = models.TextField(verbose_name="наименование")
    okpd2_code = models.CharField(max_length=32, db_index=True, verbose_name="код ОКПД2")
    okpd2_name = models.TextField(blank=True, verbose_name="наименование ОКПД2")
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "ktru"
        db_table = "ktru_position"
        ordering = ["code"]
        verbose_name = "позиция КТРУ"
        verbose_name_plural = "позиции КТРУ"
        indexes = [
            GinIndex(name="ktru_position_trgm", fields=["name"], opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name[:60]}"


class Characteristic(models.Model):
    """A characteristic of one position.

    Not deduplicated across positions on purpose: КТРУ reuses a *name* like «Материал» with
    different permitted values per position, so a shared row would merge value sets that the
    narrowing logic has to keep apart.
    """

    position = models.ForeignKey(
        Position, on_delete=models.CASCADE, related_name="characteristics"
    )
    code = models.CharField(max_length=64, verbose_name="код характеристики")
    name = models.TextField(db_index=True, verbose_name="наименование")
    # "1" качественная, "2" количественная — `apps.ktru.domain.enums.CharacteristicType`.
    type = models.CharField(max_length=2, verbose_name="тип")
    is_required = models.BooleanField(default=False, verbose_name="обязательная")

    class Meta:
        app_label = "ktru"
        db_table = "ktru_characteristic"
        verbose_name = "характеристика"
        verbose_name_plural = "характеристики"
        indexes = [
            GinIndex(name="ktru_char_name_trgm", fields=["name"], opclasses=["gin_trgm_ops"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["position", "code"], name="ktru_characteristic_unique"
            )
        ]

    def __str__(self) -> str:
        return self.name


class PermittedValue(models.Model):
    """One value a characteristic may take — a text, an exact number or an interval."""

    characteristic = models.ForeignKey(
        Characteristic, on_delete=models.CASCADE, related_name="values"
    )
    # "text" | "exact" | "range" — `apps.ktru.domain.enums.ValueKind`.
    kind = models.CharField(max_length=8, verbose_name="вид значения")
    text = models.TextField(blank=True, verbose_name="текстовое значение")
    exact = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True, verbose_name="точное значение"
    )
    minimum = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    maximum = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    min_inclusive = models.BooleanField(default=True)
    max_inclusive = models.BooleanField(default=True)
    unit_code = models.CharField(max_length=16, blank=True, verbose_name="код ОКЕИ")
    unit_name = models.CharField(max_length=120, blank=True, verbose_name="единица измерения")

    class Meta:
        app_label = "ktru"
        db_table = "ktru_permitted_value"
        verbose_name = "допустимое значение"
        verbose_name_plural = "допустимые значения"

    def __str__(self) -> str:
        return self.text or f"{self.exact or ''}{self.minimum or ''}–{self.maximum or ''}"
