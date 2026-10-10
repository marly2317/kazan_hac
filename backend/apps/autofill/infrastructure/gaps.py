"""Накопление наблюдений о нехватке в справочнике."""

from __future__ import annotations

import logging

from django.db.models import F
from django.utils import timezone

from apps.autofill.domain.gaps import ReferenceGap as ReferenceGapEntity
from apps.autofill.infrastructure.models import ReferenceGap as ReferenceGapRow

logger = logging.getLogger(__name__)


class DjangoGapRecorder:
    """Счётчик наблюдений в базе.

    `update_or_create` на наблюдение, а не массовая вставка: наблюдений за один разбор —
    единицы, а `bulk_create(update_conflicts=...)` не умеет инкремент существующего счётчика,
    ради которого таблица и заведена.

    Инкремент через `F`, а не чтением и записью: разборы идут параллельно, и
    «прочитать 5, записать 6» теряет наблюдения ровно тогда, когда сервисом пользуются.
    """

    def record(self, gaps: list[ReferenceGapEntity]) -> None:
        for gap in gaps:
            try:
                updated = ReferenceGapRow.objects.filter(
                    term=gap.term, kind=gap.kind.value, okpd2_code=gap.okpd2_code
                ).update(
                    seen_count=F("seen_count") + 1,
                    last_seen=timezone.now(),
                    last_description=gap.description,
                    position_code=gap.position_code,
                    position_name=gap.position_name,
                )
                if not updated:
                    ReferenceGapRow.objects.create(
                        term=gap.term,
                        kind=gap.kind.value,
                        okpd2_code=gap.okpd2_code,
                        position_code=gap.position_code,
                        position_name=gap.position_name,
                        last_description=gap.description,
                    )
            except Exception:
                # Сбор наблюдений не вправе уронить разбор: пользователь пришёл за кодом КТРУ,
                # а не за нашей статистикой.
                logger.exception("Не удалось записать пробел справочника: %s", gap.term)
