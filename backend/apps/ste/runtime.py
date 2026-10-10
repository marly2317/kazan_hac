"""Компоненты процесса: база знаний, корректор, поиск, LLM-клиент — по одному на процесс.

Каждый строится лениво при первом обращении: база знаний и эмбеддер — самое дорогое, что есть
в процессе, и платить за них при ``manage.py migrate`` незачем. Без Django: этим же пользуются
офлайн-скрипты и замер качества.
"""

from __future__ import annotations

from functools import lru_cache

from apps.ste import config
from apps.ste.domain.kb import KnowledgeBase


@lru_cache(maxsize=1)
def knowledge_base() -> KnowledgeBase:
    from apps.ste.kb_build.build import load

    return load(config.KB_DIR)


class _NoCorrection:
    """Корректор, который ничего не меняет: модуль опечаток недоступен или выключен."""

    def correct(self, text: str):
        from types import SimpleNamespace

        return SimpleNamespace(original=text, corrected=text, fixes=[])


@lru_cache(maxsize=1)
def corrector():
    if not config.TYPOS_ENABLED:
        return _NoCorrection()
    try:
        from apps.ste.application.typos import get_corrector
    except ImportError:
        return _NoCorrection()
    return get_corrector(knowledge_base())


@lru_cache(maxsize=1)
def retriever():
    from apps.ste.application.retrieval import load_retriever

    return load_retriever(knowledge_base())


@lru_cache(maxsize=1)
def llm_client():
    if not config.LLM_ENABLED:
        return None
    from apps.ste.infrastructure.llm_client import get_llm_client

    return get_llm_client()


def reset() -> None:
    """Сбросить всё после пересборки базы знаний (новые синонимы, векторы)."""
    for factory in (knowledge_base, corrector, retriever, llm_client):
        factory.cache_clear()
