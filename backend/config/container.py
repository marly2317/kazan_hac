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

from apps.autofill.application.use_cases.analyze_description import AnalyzeDescription
from apps.autofill.application.use_cases.record_gaps import RecordReferenceGaps
from apps.autofill.application.use_cases.suggest_terms import SuggestTerms
from apps.autofill.infrastructure.catalog import DjangoKtruCatalog
from apps.autofill.infrastructure.gaps import DjangoGapRecorder
from apps.autofill.infrastructure.suggest_index import KtruSuggestionIndex
from apps.autofill.infrastructure.vocabulary import CachedReferenceVocabulary
from apps.catalog.application.use_cases.search_products import SearchProducts
from apps.catalog.infrastructure.repositories import DjangoProductRepository
from apps.catalog.infrastructure.vocabulary import CatalogVocabulary
from apps.ktru.infrastructure.lexicon import KtruLexicon
from apps.ktru.infrastructure.search.client import available as search_available
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


@lru_cache(maxsize=1)
def dictionary_rewriter():
    """Словарная стадия исправления: `term suggester` движка, иначе SymSpell в памяти.

    Движку передана генерация кандидатов — то есть ровно то, что стоит памяти: SymSpell
    предвычисляет удаления по всему словарю и держит результат в каждом процессе. Правила
    выбора между кандидатами остались доменными в обеих реализациях, см.
    `spellcheck/infrastructure/opensearch_suggester.py`.

    **Условия два, и второе неочевидно.** Стадия на движке исправляет к словам своего индекса,
    а индекс собран из КТРУ — она не умеет исправлять к произвольному переданному словарю, как
    умеет SymSpell. Поэтому она годится только тогда, когда словарь в игре и есть КТРУ. Пока
    справочник не импортирован, `ktru_vocabulary()` намеренно подставляет демонстрационный
    каталог, и исправлять «наутбук» к «ноутбук» способен только SymSpell: в индексе КТРУ
    ноутбуков нет. Без этой проверки внедрение словаря было бы обманом — аргумент передаётся,
    а решает не он.
    """
    if search_available() and isinstance(ktru_vocabulary(), KtruVocabulary):
        from apps.spellcheck.infrastructure.opensearch_suggester import OpenSearchRewriter

        return OpenSearchRewriter(ktru_vocabulary())
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
    from apps.ktru.infrastructure.search import client as search_client

    CatalogVocabulary.invalidate()
    KtruVocabulary.invalidate()
    KtruLexicon.invalidate()
    units.invalidate()
    layout_rewriter.cache_clear()
    symspell_rewriter.cache_clear()
    dictionary_rewriter.cache_clear()
    reference_vocabulary.cache_clear()
    ktru_lexicon.cache_clear()
    suggestion_index().invalidate()
    suggestion_index.cache_clear()
    # Доступность движка тоже решается один раз на процесс: после переиндексации индекс мог
    # появиться там, где его не было.
    search_client.forget()


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


# --- автозаполнение СТЕ ---------------------------------------------------------------------


def ktru_catalog():
    """Справочник для подбора: OpenSearch, когда он есть, иначе Postgres.

    Решение принимается **здесь и один раз**, а не внутри поиска. Недоступный движок — это
    выбор способа, а не ошибка запроса: справочник живёт в Postgres, движок производен от
    него, и поиск по источнику истины всегда возможен. Что именно сейчас отвечает, видно в
    журнале — `search/client.py` пишет причину.
    """
    if search_available():
        from apps.autofill.infrastructure.opensearch_catalog import OpenSearchKtruCatalog

        return OpenSearchKtruCatalog()
    return DjangoKtruCatalog()


@lru_cache(maxsize=1)
def ktru_lexicon() -> KtruLexicon:
    """Словарь для разделения описания на наименование и характеристики.

    Кэшируется по той же причине, что индексы корректора: строится обходом всего справочника,
    а обслуживает каждый разбор описания.
    """
    return KtruLexicon()


def analyze_description() -> AnalyzeDescription:
    """The narrowing pipeline: correct the text, then let each characteristic filter КТРУ.

    The unit normaliser is passed in rather than imported by the use case, so the whole
    narrowing algorithm stays free of Django and can be driven by in-memory doubles.

    Сужение передаётся движку, когда движок есть: условие в запросе применяется ко всему
    индексу, а перебор — только к пулу, отобранному по наименованию. На Postgres порт сужения
    не передаётся вовсе, и сценарий перебирает пул сам.
    """
    from apps.ktru.infrastructure.units import canonical_unit

    catalog = ktru_catalog()
    # Один и тот же объект в обеих ролях: он держит допустимые значения, приехавшие вместе с
    # кандидатами, и второй экземпляр о них не знал бы.
    narrowing = catalog if hasattr(catalog, "narrow") else None

    return AnalyzeDescription(
        catalog,
        correct_query(),
        canonical_unit,
        lexicon=ktru_lexicon(),
        narrowing=narrowing,
    )


@lru_cache(maxsize=1)
def suggestion_index():
    """Словарь подсказок: автомат движка, когда движок есть, иначе список в памяти процесса.

    На словаре категорий хакатона (2638 фраз, 50 КБ) список в памяти **быстрее** — 1 мкс
    против сетевого обращения, — и замер это показывал. Движку он уступает в другом: индекс в
    памяти принадлежит процессу, и три воркера держат три копии, каждую построенную при первом
    запросе. Выбор сделан в пользу одного словаря на стенд; обе реализации остаются, чтобы
    сравнение не приходилось принимать на веру.
    """
    if search_available():
        from apps.autofill.infrastructure.opensearch_suggest import OpenSearchSuggestionIndex

        return OpenSearchSuggestionIndex()
    return KtruSuggestionIndex()


def suggest_terms() -> SuggestTerms:
    return SuggestTerms(suggestion_index())


@lru_cache(maxsize=1)
def reference_vocabulary() -> CachedReferenceVocabulary:
    """Множество слов справочника. Кэшируется по той же причине, что и индексы корректора."""
    return CachedReferenceVocabulary(ktru_vocabulary())


def record_reference_gaps() -> RecordReferenceGaps | None:
    """Сбор наблюдений о нехватке в справочнике, либо ``None``, когда он выключен.

    ``None``, а не заглушка-пустышка: вызывающая сторона и так обязана решить, собирать ли
    наблюдения, и явное «возможности нет» читается в коде представления честнее, чем объект,
    который молча ничего не делает.
    """
    if not settings.COLLECT_REFERENCE_GAPS:
        return None
    return RecordReferenceGaps(DjangoGapRecorder(), reference_vocabulary())
