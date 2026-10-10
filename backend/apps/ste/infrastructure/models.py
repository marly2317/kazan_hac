"""Состояние сессии подбора (архитектура §6.2).

Сессия живёт между запросами: ответ на вопрос кнопкой пересчитывает сужение **без LLM**, и для
этого нужно помнить уже разобранное. В базе, а не в памяти процесса: gunicorn держит несколько
воркеров, и следующий запрос пользователя может прийти в другой.
"""

from __future__ import annotations

import uuid

from django.db import models


class SteSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    data = models.JSONField(default=dict, verbose_name="состояние")
    created = models.DateTimeField(auto_now_add=True, verbose_name="создана")
    updated = models.DateTimeField(auto_now=True, db_index=True, verbose_name="обновлена")

    class Meta:
        verbose_name = "сессия подбора КТРУ"
        verbose_name_plural = "сессии подбора КТРУ"

    def __str__(self) -> str:
        return str(self.id)
