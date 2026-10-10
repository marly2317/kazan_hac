"""Сбор наблюдений о том, чего не хватает в справочнике."""

from __future__ import annotations

import logging

from apps.autofill.application.ports import GapRecorder, ReferenceVocabulary
from apps.autofill.domain.entities import Analysis
from apps.autofill.domain.gaps import find_gaps

logger = logging.getLogger(__name__)


class RecordReferenceGaps:
    """Отдельный сценарий, а не часть разбора.

    Разбор отвечает пользователю; этот сценарий пишет заметку тем, кто ведёт справочник. Это
    разные задачи с разными адресатами, и разделение даёт ровно то, что просил заказчик от
    «необязательной возможности»: её можно не подключать в `container`, и разбор от этого не
    изменится ни на строчку.
    """

    def __init__(self, recorder: GapRecorder, vocabulary: ReferenceVocabulary) -> None:
        self._recorder = recorder
        self._vocabulary = vocabulary

    def execute(self, analysis: Analysis) -> None:
        try:
            gaps = find_gaps(analysis, self._vocabulary.knows)
            if gaps:
                self._recorder.record(gaps)
        except Exception:
            # Наблюдение о пробеле не стоит ответа пользователю: что бы здесь ни случилось,
            # он уже получил свой код КТРУ, и падение из-за статистики было бы абсурдом.
            logger.exception("Сбор пробелов справочника не удался")
