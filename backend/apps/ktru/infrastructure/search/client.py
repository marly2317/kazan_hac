"""Подключение к OpenSearch и ответ на вопрос «он вообще есть?».

Движок — **производный** индекс, а не источник истины: справочник живёт в Postgres, а
OpenSearch перестраивается из него командой `manage.py index_ktru`. Из этого следует всё
устройство модуля: потеря индекса не теряет данных, рассогласование лечится переиндексацией, а
недоступность движка — повод вернуться на Postgres, а не отдать пользователю ошибку.

Отсюда и :func:`available`: решение «каким способом искать» принимается в композиционном
корне один раз, а не вылетает исключением из середины разбора описания.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def client():
    """Клиент OpenSearch, либо ``None``, когда движок не настроен.

    Кэшируется на процесс: внутри живёт пул соединений, и создавать его на запрос значило бы
    платить за установку TCP-соединения каждый раз — дороже, чем сам поиск.
    """
    if not settings.OPENSEARCH_URL:
        return None

    from opensearchpy import OpenSearch

    return OpenSearch(
        hosts=[settings.OPENSEARCH_URL],
        # Повтор на другом узле смысла не имеет — узел один; повтор на том же лишь удваивает
        # время ожидания перед тем же отказом.
        max_retries=0,
        retry_on_timeout=False,
        timeout=settings.OPENSEARCH_TIMEOUT,
    )


@lru_cache(maxsize=1)
def available() -> bool:
    """Движок настроен, отвечает и содержит индекс позиций.

    Все три условия вместе, потому что любое из них по отдельности оставляет поиск
    неработоспособным, а различать их пользователю незачем: в обоих случаях ответ даёт
    Postgres. В журнал причина пишется — там она нужна.

    Проверяется один раз на процесс. Опрашивать движок на каждый запрос значило бы добавить
    сетевое обращение к каждому поиску ради сведений, которые меняются раз в жизни стенда;
    после переиндексации кэш сбрасывает :func:`forget`.
    """
    connection = client()
    if connection is None:
        logger.info("OPENSEARCH_URL не задан — поиск идёт по Postgres")
        return False

    from apps.ktru.infrastructure.search.indexes import POSITIONS

    try:
        if not connection.indices.exists(index=POSITIONS.name):
            logger.warning(
                "Индекс %s не создан — поиск идёт по Postgres. "
                "Создать: manage.py index_ktru",
                POSITIONS.name,
            )
            return False
    except Exception as error:  # noqa: BLE001 — причина любая, решение одно
        logger.warning("OpenSearch недоступен (%s) — поиск идёт по Postgres", error)
        return False

    return True


def forget() -> None:
    """Забыть решение о доступности. Вызывается после переиндексации."""
    available.cache_clear()
