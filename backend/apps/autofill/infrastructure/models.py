"""Хранилище наблюдений о нехватке в справочнике.

Одна строка — не один запрос пользователя, а **одно наблюдение, накопленное по запросам**:
ключ `(термин, вид, группа ОКПД2)`, счётчик и последний пример. Иначе таблица за день показа
превратилась бы в журнал обращений, в котором ничего не найти, а главный вопрос — «что
спрашивают чаще всего» — требовал бы агрегирующего запроса.
"""

from __future__ import annotations

from django.db import models

from apps.autofill.domain.gaps import GAP_KIND_CHOICES


class GapStatus(models.TextChoices):
    NEW = "new", "новое"
    ACCEPTED = "accepted", "принято в работу"
    REJECTED = "rejected", "отклонено"


class ReferenceGap(models.Model):
    """Чего не хватило в КТРУ, чтобы объяснить описание пользователя."""

    term = models.CharField(max_length=120, verbose_name="термин")
    kind = models.CharField(max_length=32, choices=GAP_KIND_CHOICES, verbose_name="вид")
    okpd2_code = models.CharField(
        max_length=32, blank=True, db_index=True, verbose_name="код ОКПД2"
    )
    position_code = models.CharField(max_length=64, blank=True, verbose_name="код позиции")
    position_name = models.TextField(blank=True, verbose_name="наименование позиции")

    seen_count = models.PositiveIntegerField(default=1, verbose_name="сколько раз встретилось")
    first_seen = models.DateTimeField(auto_now_add=True, verbose_name="впервые")
    last_seen = models.DateTimeField(auto_now=True, verbose_name="последний раз")
    last_description = models.TextField(blank=True, verbose_name="последнее описание")

    status = models.CharField(
        max_length=16,
        choices=GapStatus.choices,
        default=GapStatus.NEW,
        db_index=True,
        verbose_name="статус",
    )
    note = models.TextField(blank=True, verbose_name="комментарий")

    class Meta:
        app_label = "autofill"
        db_table = "autofill_reference_gap"
        # Самое частое — сверху: отчёт читают, чтобы решить, чем заняться в первую очередь.
        ordering = ["-seen_count", "term"]
        verbose_name = "пробел в справочнике"
        verbose_name_plural = "пробелы в справочнике"
        constraints = [
            models.UniqueConstraint(
                fields=["term", "kind", "okpd2_code"], name="autofill_gap_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.term} ({self.get_kind_display()}) ×{self.seen_count}"
