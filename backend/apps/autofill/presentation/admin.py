"""Бэк-офис: что пользователи спрашивают, а справочник не покрывает."""

from __future__ import annotations

from django.contrib import admin

from apps.autofill.infrastructure.models import GapStatus, ReferenceGap


@admin.register(ReferenceGap)
class ReferenceGapAdmin(admin.ModelAdmin):
    list_display = (
        "term",
        "kind",
        "seen_count",
        "okpd2_code",
        "position_name",
        "status",
        "last_seen",
    )
    list_filter = ("kind", "status", "okpd2_code")
    search_fields = ("term", "position_name", "position_code", "last_description")
    readonly_fields = (
        "term",
        "kind",
        "okpd2_code",
        "position_code",
        "position_name",
        "seen_count",
        "first_seen",
        "last_seen",
        "last_description",
    )
    # Правятся только два поля: это журнал наблюдений, а не справочник. Остальное записал
    # сервис, и редактирование сделало бы цифры недостоверными.
    fields = (*readonly_fields, "status", "note")
    list_editable = ("status",)
    actions = ("mark_accepted", "mark_rejected")

    def has_add_permission(self, request) -> bool:
        """Строки пишет сервис, руками их заводить нечем: все поля наблюдения — только чтение.

        Кнопка «Добавить» без этого ведёт на форму, где заполнить можно лишь статус и
        комментарий, то есть создать наблюдение без наблюдения.
        """
        return False

    @admin.action(description="Принять в работу")
    def mark_accepted(self, request, queryset) -> None:
        queryset.update(status=GapStatus.ACCEPTED)

    @admin.action(description="Отклонить")
    def mark_rejected(self, request, queryset) -> None:
        queryset.update(status=GapStatus.REJECTED)
