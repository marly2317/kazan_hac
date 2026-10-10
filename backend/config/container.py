"""The composition root.

Use cases are wired to their infrastructure in exactly one place. Views import from here, so no
presentation module ever names a repository class, and a test can swap an implementation by
patching one function. Each call builds a fresh, stateless object: there is nothing to keep
between requests, and a module-level singleton would outlive the ORM connection it closed over.
"""

from __future__ import annotations

from functools import lru_cache

from django.conf import settings
from django.contrib.auth import get_user_model

from apps.catalog.application.use_cases.search_products import SearchProducts
from apps.catalog.infrastructure.repositories import DjangoProductRepository
from apps.catalog.infrastructure.vocabulary import CatalogVocabulary
from apps.ktru.infrastructure.vocabulary import KtruVocabulary
from apps.spellcheck.application.use_cases.correct_query import CorrectQuery
from apps.spellcheck.domain.enums import CorrectionSource
from apps.spellcheck.infrastructure.cache import DjangoCorrectionCache
from apps.spellcheck.infrastructure.keyboard import KeyboardLayoutRewriter
from apps.spellcheck.infrastructure.llm import LlmRewriter
from apps.spellcheck.infrastructure.symspell_index import SymSpellRewriter
from apps.users.application.use_cases.authenticate_user import AuthenticateUser
from apps.users.application.use_cases.get_current_user import GetCurrentUser
from apps.users.application.use_cases.register_user import RegisterUser
from apps.users.infrastructure.repositories import (
    DjangoPasswordHasher,
    DjangoPasswordPolicy,
    DjangoUserRepository,
)


def user_repository() -> DjangoUserRepository:
    return DjangoUserRepository()


def password_hasher() -> DjangoPasswordHasher:
    return DjangoPasswordHasher()


def password_policy() -> DjangoPasswordPolicy:
    return DjangoPasswordPolicy()


def user_model():
    """The account model, for the two places Django itself needs a row (``login``, the admin)."""
    return get_user_model()


def register_user() -> RegisterUser:
    return RegisterUser(user_repository(), password_hasher(), password_policy())


def authenticate_user() -> AuthenticateUser:
    return AuthenticateUser(user_repository(), password_hasher())


def get_current_user() -> GetCurrentUser:
    return GetCurrentUser(user_repository())


# --- catalogue and search ------------------------------------------------------------------
#
# The two spelling stages below are cached per process, unlike everything above. They are the
# exception that proves the rule: each owns a prebuilt in-memory index over the whole
# catalogue, and rebuilding it per request would cost far more than the search it serves.
# `invalidate()` on the vocabulary plus a fresh index is what a catalogue change triggers.


def product_repository() -> DjangoProductRepository:
    return DjangoProductRepository()


def catalog_vocabulary() -> CatalogVocabulary:
    return CatalogVocabulary()


def ktru_vocabulary() -> KtruVocabulary:
    """The words corrections aim at.

    The КТРУ reference, not the demo catalogue: the task is to land the user on a КТРУ code,
    so the only useful correction is one towards a word that reference actually uses. Falls
    back to the catalogue while КТРУ has not been imported, so a fresh database still corrects
    rather than silently doing nothing.
    """
    from apps.ktru.infrastructure.models import Position

    if Position.objects.exists():
        return KtruVocabulary()
    return CatalogVocabulary()


@lru_cache(maxsize=1)
def layout_rewriter() -> KeyboardLayoutRewriter:
    return KeyboardLayoutRewriter(ktru_vocabulary())


@lru_cache(maxsize=1)
def symspell_rewriter() -> SymSpellRewriter:
    return SymSpellRewriter(ktru_vocabulary())


def dictionary_rewriter():
    """Словарная стадия исправления опечаток демо-каталога: SymSpell в памяти процесса."""
    return symspell_rewriter()


@lru_cache(maxsize=1)
def llm_rewriter() -> LlmRewriter:
    return LlmRewriter(
        base_url=settings.LLM_BASE_URL,
        model=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY,
        cache=DjangoCorrectionCache(),
        timeout=settings.LLM_TIMEOUT,
        disable_reasoning=settings.LLM_DISABLE_REASONING,
    )


def reset_spelling_indexes() -> None:
    """Drop the cached indexes after the catalogue changes."""
    from apps.ktru.infrastructure import units

    CatalogVocabulary.invalidate()
    KtruVocabulary.invalidate()
    units.invalidate()
    layout_rewriter.cache_clear()
    symspell_rewriter.cache_clear()


def correct_query() -> CorrectQuery:
    """The correction pipeline, cheapest stage first.

    Order is the design: a layout flip is a lookup table, a dictionary fix is an in-memory
    index, and only a query neither of them could improve is worth a network call. The LLM
    stage is simply absent when no key is configured, so the pipeline degrades to the two
    deterministic stages rather than failing.
    """
    stages = [
        (CorrectionSource.LAYOUT, layout_rewriter()),
        (CorrectionSource.DICTIONARY, dictionary_rewriter()),
    ]
    llm = llm_rewriter()
    if llm.enabled:
        stages.append((CorrectionSource.LLM, llm))
    return CorrectQuery(stages)


def search_products() -> SearchProducts:
    return SearchProducts(product_repository(), correct_query())


# --- подбор КТРУ и автозаполнение СТЕ (apps/ste) --------------------------------------------


@lru_cache(maxsize=1)
def ste_pipeline():
    """Пайплайн подбора КТРУ: компоненты процесса из `apps.ste.runtime` + сессии в Postgres.

    Один на процесс: внутри только ссылки на долгоживущие компоненты (база знаний, индекс,
    эмбеддер, LLM-клиент), а состояние запроса хранится в сессии, не в объекте.
    """
    from apps.ste import runtime
    from apps.ste.application.pipeline import Deps, Pipeline
    from apps.ste.infrastructure.session_store import DjangoSessionStore

    return Pipeline(
        Deps(
            kb=runtime.knowledge_base(),
            corrector=runtime.corrector(),
            retriever=runtime.retriever(),
            llm=runtime.llm_client(),
            reranker=runtime.reranker(),
            store=DjangoSessionStore(),
        )
    )
