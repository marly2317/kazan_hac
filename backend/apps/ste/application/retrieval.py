"""Поиск семейств КТРУ по тексту поставщика (архитектура §3.9, §4.0).

Два применения:

* **предпросмотр** — топ-5 семейств «похоже на…», пока работает LLM-1 (на решение не влияет);
* **кандидаты для LLM-1** в режиме ``top_k`` и признаки «быстрого пути» (:meth:`confidence`):
  когда лексика и вектор единодушны, вызов LLM-1 ничего не добавляет.

Индекс — полные карточки семейств (§3.8), в памяти: при ~370 документах внешний движок не
нужен. Два ранжирования сливаются по RRF (k=60):

* **BM25** (k1=1,5, b=0,75) по основам слов (Snowball, русский), с обрезкой основы до
  :data:`PREFIX` букв — так «аккумуляторный» и «от аккумулятора», «табуретка» и «табурет»
  совпадают, а стеммер этого не делает;
* **косинус** вектора запроса к заранее посчитанным векторам карточек (Qwen3-Embedding) —
  ловит то, где общих основ нет вовсе («болгарка» → «Шлифмашина»).

Перед поиском запрос чистится **правилами**: числа с единицами («125мм», «840 Вт»),
латиница (бренды и модели: Makita, GA5030, PSR) и кириллические бренды. Они не называют тип
товара, а вектор от них уводит в сторону.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import snowballstemmer

from apps.ste.domain import units as U
from apps.ste.domain.cards import full_card
from apps.ste.domain.kb import Family, KnowledgeBase
from apps.ste.domain.model import FamilyHit

logger = logging.getLogger(__name__)

# --- параметры (подобраны на запросах из tests/ste/test_retrieval.py) ----------------------

BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60

#: Вес голоса вектора в RRF (у BM25 — 1). Подобран замером: на реальных наименованиях СТЕ
#: из выгрузок (46 шт.) верное семейство первым — BM25 80%, вектор 85%, слияние с весом 0,6 —
#: 89%; на тест-кейсах — 88%; в топ-30 для LLM-1 — 98–99%. Больший вес тянет к «похожим»
#: соседним семействам (1,6 → 83%), меньший теряет выигрыш вектора на живых названиях.
VECTOR_WEIGHT = float(os.environ.get("STE_VECTOR_WEIGHT", "0.6"))

#: always — вектор запроса считается всегда (машина с GPU: единицы мс); auto — только когда
#: лексика не уверена (CPU: ~0,7 с на запрос); off — только BM25.
VECTOR_MODE = os.environ.get("STE_VECTOR_MODE", "always")

#: Вектор запроса стоит сотни миллисекунд на CPU, поэтому в режиме ``auto`` он считается,
#: только когда лексика не уверена: BM25 нашёл меньше AUTO_MIN_HITS семейств или лидер
#: опережает второе место меньше чем в AUTO_RATIO раз.
AUTO_MIN_HITS = 3
AUTO_RATIO = 1.3
#: Основа слова обрезается до стольких букв: русская словоизменительная и словообразовательная
#: морфология (прилагательное ↔ существительное, уменьшительные) стеммером не снимается.
PREFIX = 7
#: Название и синонимы — самое точное, что есть в карточке; их вес в документе выше.
NAME_BOOST = 2
SYNONYM_BOOST = 2
#: Вес покрытия названия семейства запросом в лексическом скоре (см. ``Retriever._lexical``).
NAME_COVERAGE_BONUS = 0.5
#: Кэш векторов запросов: живой ввод и следующий за ним «анализ» повторяют один и тот же текст.
QUERY_CACHE = 512

#: Пороги «быстрого пути» (без LLM-1); подобраны так, чтобы на наборе запросов не было ни
#: одного неверного «уверенного» ответа, см. ``test_strong_is_precise``.
STRONG_COSINE_MARGIN = 0.03
STRONG_NAME_OVERLAP = 1.0
STRONG_BM25_RATIO = 1.5

#: Слова без смысла для типа товара: служебные части речи и рекламный шум поставщика.
STOP_WORDS = frozenset(
    """
    и в во не на с со для из изо по от до к ко о об обо у за под над при без или либо а но же ли
    бы это как что то все всех так его ее их он она оно они мы вы я через между также
    шт штук штука штуки товар товара поставка продаю продам продается предлагаем
    новый новая новое новые новых оригинал оригинальный гарантия цена руб рублей артикул арт
    модель модели марка бренд производитель производства фирма фирмы качественный качество
    отличный хороший
    """.split()
)

#: Латиница → кириллица для слов, набранных вперемешку («cтол» с латинской «c»).
_HOMOGLYPHS = str.maketrans("aceopxykmthb", "асеорхукмтнв")
_RAW_TOKEN = re.compile(r"[0-9a-zа-я]+(?:[.,/][0-9a-zа-я]+)*")
_CYRILLIC = re.compile(r"[а-я]")
_LATIN = re.compile(r"[a-z]")
_DIGIT = re.compile(r"\d")

_STEMMER = snowballstemmer.stemmer("russian")
_STEM_LOCK = threading.Lock()


def fold(text: str) -> str:
    return text.casefold().replace("ё", "е")


@lru_cache(maxsize=65536)
def _stem(word: str) -> str:
    # Стеммер Snowball хранит состояние в объекте — между потоками только под замком.
    with _STEM_LOCK:
        return _STEMMER.stemWord(word)


def default_brands() -> frozenset[str]:
    """Бренды из модуля LLM-метаданных (там их список ведут); без него — пусто."""
    try:
        from apps.ste.application.family_meta import DEFAULT_BRANDS
    except ImportError:  # pragma: no cover — модуль ведёт лид, здесь только чтение
        return frozenset()
    return frozenset(fold(brand) for brand in DEFAULT_BRANDS)


def clean_query(text: str, brands: Iterable[str] = ()) -> str:
    """Убрать из запроса то, что не называет тип товара (архитектура §3.9).

    * числа и всё с цифрами: «125мм», «GA5030», «18В», «1200х600»;
    * единицу сразу после числа: «840 Вт», «2 Ач», «50 м»;
    * латиницу: бренды и модели (Makita, PSR, SDS) — в карточках КТРУ латиницы почти нет;
    * кириллические бренды (Интерскол, Зубр) из ``brands``.

    Слова, набранные вперемешку кириллицей и латиницей, чинятся заменой похожих букв.
    """
    brand_set = {fold(brand) for brand in brands}
    kept: list[str] = []
    after_number = False
    for token in _RAW_TOKEN.findall(fold(text)):
        if _DIGIT.search(token):
            after_number = True
            continue
        if after_number and U.parse_unit(token) is not None:
            after_number = False
            continue
        after_number = False
        if _LATIN.search(token):
            if not _CYRILLIC.search(token):
                continue
            token = token.translate(_HOMOGLYPHS)
            if _LATIN.search(token):
                continue
        if token in brand_set:
            continue
        kept.append(token)
    return " ".join(kept)


#: Приставки сложных слов: «Электропаяльник» должен находиться по «паяльник», «Электролобзик» —
#: по «лобзик», «спецодежда» — по «одежда». Слово даёт оба терма: целиком и без приставки.
COMPOUND_PREFIXES = ("электро", "вибро", "термо", "пневмо", "бензо", "угло", "спец")
_MIN_COMPOUND_TAIL = 5


def _words(word: str) -> list[str]:
    for head in COMPOUND_PREFIXES:
        if word.startswith(head) and len(word) - len(head) >= _MIN_COMPOUND_TAIL:
            return [word, word[len(head) :]]
    return [word]


def analyze(text: str, prefix: int | None = PREFIX) -> list[str]:
    """Текст → термы BM25: слова кириллицей, без стоп-слов, основа, обрезанная до ``prefix``."""
    terms: list[str] = []
    for token in re.findall(r"[а-я]+", fold(text)):
        if len(token) < 2 or token in STOP_WORDS:
            continue
        for word in _words(token):
            stem = _stem(word)
            terms.append(stem[:prefix] if prefix else stem)
    return terms


# --- BM25 ----------------------------------------------------------------------------------


class Bm25:
    """BM25 Okapi с idf Lucene (всегда > 0): ~370 документов — словарь в памяти за миллисекунды."""

    def __init__(self, docs: list[list[str]], k1: float = BM25_K1, b: float = BM25_B) -> None:
        self.k1, self.b = k1, b
        self.size = len(docs)
        lengths = np.array([len(doc) for doc in docs], dtype=np.float64)
        self.avg_len = float(lengths.mean()) if len(docs) and lengths.sum() else 1.0
        self._norm = k1 * (1 - b + b * lengths / self.avg_len) if len(docs) else lengths
        self.postings: dict[str, list[tuple[int, int]]] = {}
        for index, doc in enumerate(docs):
            counts: dict[str, int] = {}
            for term in doc:
                counts[term] = counts.get(term, 0) + 1
            for term, count in counts.items():
                self.postings.setdefault(term, []).append((index, count))
        self.idf = {
            term: math.log(1 + (self.size - len(posting) + 0.5) / (len(posting) + 0.5))
            for term, posting in self.postings.items()
        }

    def scores(self, terms: Iterable[str]) -> np.ndarray:
        result = np.zeros(self.size, dtype=np.float64)
        for term in dict.fromkeys(terms):
            posting = self.postings.get(term)
            if not posting:
                continue
            idf = self.idf[term]
            for index, tf in posting:
                result[index] += idf * tf * (self.k1 + 1) / (tf + self._norm[index])
        return result


# --- поиск ---------------------------------------------------------------------------------


@dataclass
class _SearchInfo:
    """Что поиск знает о запросе сверх :class:`FamilyHit` — для :meth:`Retriever.confidence`."""

    terms: frozenset[str]
    bm25_scores: dict[str, float]
    used_vectors: bool
    timings_ms: dict[str, float] = field(default_factory=dict)


class Retriever:
    """BM25 + векторы + RRF по карточкам семейств. Потокобезопасен на чтение."""

    def __init__(
        self,
        kb: KnowledgeBase,
        embedder=None,
        vectors=None,
        *,
        brands: Iterable[str] | None = None,
        prefix: int | None = PREFIX,
        rrf_k: int = RRF_K,
    ) -> None:
        self.kb = kb
        self.prefix = prefix
        self.rrf_k = rrf_k
        self.brands = frozenset(fold(b) for b in brands) if brands is not None else default_brands()
        self.families: list[Family] = [
            family for family in kb.families.values() if family.role in {"family", "generic"}
        ]
        self._index = {family.id: index for index, family in enumerate(self.families)}
        self._generic = np.array([family.role == "generic" for family in self.families])
        self._bm25 = Bm25([self._doc_terms(family) for family in self.families])
        self._name_terms = [frozenset(analyze(f.name, prefix)) for f in self.families]

        self.embedder = embedder
        #: Карточки изменились после расчёта векторов (новые синонимы) — пора пересчитать.
        self.vectors_stale = False
        self._matrix: np.ndarray | None = None
        self._rows: np.ndarray | None = None  # строка матрицы → индекс семейства
        if vectors is not None and embedder is not None:
            self._attach_vectors(vectors)

        self._query_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._info: OrderedDict[int, tuple[FamilyHit, _SearchInfo]] = OrderedDict()
        self._lock = threading.Lock()

    # --- построение ------------------------------------------------------------------------

    def _doc_terms(self, family: Family) -> list[str]:
        terms = analyze(full_card(family), self.prefix)
        terms += analyze(family.name, self.prefix) * NAME_BOOST
        if family.synonyms:
            terms += analyze(" ".join(family.synonyms), self.prefix) * SYNONYM_BOOST
        return terms

    def _attach_vectors(self, vectors) -> None:
        model = getattr(self.embedder, "model_name", "")
        if vectors.model and model and not same_model(vectors.model, model):
            logger.warning(
                "Векторы посчитаны моделью %s, а эмбеддер — %s: поиск только лексический",
                vectors.model,
                model,
            )
            return
        rows, positions = [], []
        for row, family_id in enumerate(vectors.ids):
            index = self._index.get(family_id)
            if index is not None:
                rows.append(row)
                positions.append(index)
        if not rows:
            logger.warning("Векторы не относятся ни к одному семейству базы — не используются")
            return
        missing = len(self.families) - len(rows)
        if missing:
            logger.warning("У %d семейств нет векторов — пересчитайте embed_ste_kb", missing)
        from apps.ste.kb_build.embed import cards_sha256, embeddable

        self.vectors_stale = bool(vectors.card_sha256) and vectors.card_sha256 != cards_sha256(
            embeddable(self.kb)
        )
        if self.vectors_stale:
            logger.warning("Карточки изменились после расчёта векторов — пересчитайте embed_ste_kb")
        self._matrix = np.ascontiguousarray(vectors.matrix[rows], dtype=np.float32)
        self._rows = np.array(positions, dtype=np.int64)

    @property
    def has_vectors(self) -> bool:
        return self._matrix is not None

    def warmup(self) -> None:
        """Загрузить модель заранее: первый запрос иначе ждёт загрузку весов (секунды)."""
        if self.has_vectors:
            self._query_vector("шуруповерт")

    def _lexical(self, terms: list[str]) -> np.ndarray:
        """BM25 × (1 + α · покрытие названия): «ветровка» — это «Ветровка», а не «Ветровка детская».

        BM25 не различает, назвал ли запрос семейство целиком или только его часть: у названий
        «Ветровка» и «Ветровка детская» одна и та же основа совпала один раз. Доля основ
        названия, которые есть в запросе, разводит их, не трогая порядок далёких документов.
        """
        scores = self._bm25.scores(terms)
        if NAME_COVERAGE_BONUS:
            query = set(terms)
            for index in np.flatnonzero(scores):
                name = self._name_terms[index]
                if name:
                    scores[index] *= 1 + NAME_COVERAGE_BONUS * len(name & query) / len(name)
        return scores

    # --- вектор запроса --------------------------------------------------------------------

    def _query_vector(self, query: str) -> np.ndarray | None:
        with self._lock:
            cached = self._query_cache.get(query)
            if cached is not None:
                self._query_cache.move_to_end(query)
                return cached
        encoded = self.embedder.encode_queries([query]) if self.embedder is not None else None
        if encoded is None:
            return None
        vector = np.asarray(encoded[0], dtype=np.float32)
        if self._matrix is not None and vector.shape[0] != self._matrix.shape[1]:
            logger.warning(
                "Размерность запроса %d ≠ векторов %d — поиск только лексический",
                vector.shape[0],
                self._matrix.shape[1],
            )
            self._matrix = None
            return None
        with self._lock:
            self._query_cache[query] = vector
            if len(self._query_cache) > QUERY_CACHE:
                self._query_cache.popitem(last=False)
        return vector

    def cosines(self, query: str) -> np.ndarray | None:
        """Косинус запроса к каждому семейству (``nan`` — у семейства нет вектора)."""
        if self._matrix is None or self._rows is None or not query:
            return None
        vector = self._query_vector(query)
        if vector is None or self._matrix is None:
            return None
        result = np.full(len(self.families), np.nan, dtype=np.float64)
        result[self._rows] = self._matrix @ vector
        return result

    # --- поиск -----------------------------------------------------------------------------

    def _allowed(self, include_generic: bool) -> np.ndarray:
        if include_generic:
            return np.ones(len(self.families), dtype=bool)
        return ~self._generic

    def search(
        self,
        text: str,
        k: int = 30,
        include_generic: bool = False,
        *,
        vectors: bool | str | None = None,
    ) -> list[FamilyHit]:
        """Топ-``k`` семейств для текста; пустой список — в тексте нет слов о типе товара.

        ``vectors=False`` — только BM25 (доли миллисекунды): для живого ввода, если вектор
        запроса на этом CPU дорог. Без векторов признаки :meth:`confidence` строже.
        """
        return self.search_timed(text, k, include_generic, vectors=vectors)[0]

    def search_timed(
        self,
        text: str,
        k: int = 30,
        include_generic: bool = False,
        *,
        vectors: bool | str | None = None,
    ) -> tuple[list[FamilyHit], dict[str, float]]:
        """То же, что :meth:`search`, плюс время шагов в мс: ``clean, bm25, vector, fuse``."""
        timings: dict[str, float] = {}
        started = time.perf_counter()
        query = clean_query(text or "", self.brands)
        terms = analyze(query, self.prefix)
        timings["clean"] = _ms(started)
        if not terms or k <= 0:
            return [], timings

        allowed = self._allowed(include_generic)
        step = time.perf_counter()
        bm25 = self._lexical(terms)
        bm25_order = [
            int(i) for i in np.argsort(-bm25, kind="stable") if bm25[i] > 0 and allowed[i]
        ]
        timings["bm25"] = _ms(step)

        step = time.perf_counter()
        if vectors is None:
            vectors = {"always": True, "off": False}.get(VECTOR_MODE, "auto")
        if vectors == "auto":
            vectors = _lexically_unsure(bm25, bm25_order)
        cosines = self.cosines(query) if vectors else None
        vector_order: list[int] = []
        if cosines is not None:
            valid = allowed & ~np.isnan(cosines)
            ranked = np.argsort(-np.where(valid, cosines, -np.inf), kind="stable")
            vector_order = [int(i) for i in ranked if valid[i]]
        timings["vector"] = _ms(step)

        step = time.perf_counter()
        hits = self._fuse(bm25, bm25_order, cosines, vector_order, k)
        if hits:
            info = _SearchInfo(
                terms=frozenset(terms),
                bm25_scores={self.families[i].id: float(bm25[i]) for i in bm25_order[: max(k, 2)]},
                used_vectors=cosines is not None,
                timings_ms=timings,
            )
            with self._lock:
                self._info[id(hits[0])] = (hits[0], info)
                if len(self._info) > QUERY_CACHE:
                    self._info.popitem(last=False)
        timings["fuse"] = _ms(step)
        timings["total"] = _ms(started)
        return hits, timings

    def _fuse(
        self,
        bm25: np.ndarray,
        bm25_order: list[int],
        cosines: np.ndarray | None,
        vector_order: list[int],
        k: int,
    ) -> list[FamilyHit]:
        """RRF: ``Σ 1 / (k + ранг)``; скор нормирован на максимум (первое место везде = 1)."""
        bm25_rank = {index: rank for rank, index in enumerate(bm25_order, start=1)}
        vector_rank = {index: rank for rank, index in enumerate(vector_order, start=1)}
        fused: dict[int, float] = {}
        # Без лексики вектор — единственный голос и весит полностью.
        weight = VECTOR_WEIGHT if bm25_order else 1.0
        for ranks, share in ((bm25_rank, 1.0), (vector_rank, weight)):
            for index, rank in ranks.items():
                fused[index] = fused.get(index, 0.0) + share / (self.rrf_k + rank)
        best = (1.0 if bm25_order else 0.0) + (weight if cosines is not None else 0.0)
        best = max(best, 1e-9) / (self.rrf_k + 1)

        def order(index: int) -> tuple:
            cosine = cosines[index] if cosines is not None else 0.0
            return (-fused[index], -bm25[index], -(0.0 if np.isnan(cosine) else cosine))

        hits = []
        for index in sorted(fused, key=order)[:k]:
            family = self.families[index]
            cosine = None
            if cosines is not None and not np.isnan(cosines[index]):
                cosine = round(float(cosines[index]), 4)
            hits.append(
                FamilyHit(
                    family_id=family.id,
                    short_id=family.short_id,
                    name=family.name,
                    score=round(fused[index] / best, 4),
                    bm25_rank=bm25_rank.get(index),
                    vector_rank=vector_rank.get(index),
                    cosine=cosine,
                )
            )
        return hits

    # --- уверенность -----------------------------------------------------------------------

    def _name_of(self, family_id: str) -> frozenset[str]:
        index = self._index.get(family_id)
        return self._name_terms[index] if index is not None else frozenset()

    def name_overlap(self, terms: Iterable[str], family_id: str) -> float:
        """Доля основ названия семейства, которые есть в запросе (0 — ни одной, 1 — все)."""
        index = self._index.get(family_id)
        if index is None:
            return 0.0
        name = self._name_terms[index]
        if not name:
            return 0.0
        return len(name & set(terms)) / len(name)

    def confidence(self, hits: list[FamilyHit], text: str | None = None) -> dict:
        """Признаки «быстрого пути» для лидера поиска (архитектура §4.1, наше улучшение).

        * ``top1_cosine``, ``top2_cosine``, ``cosine_margin`` — косинус лидера и отрыв от
          лучшего из остальных;
        * ``agree`` — лидер первый и в BM25, и по вектору;
        * ``name_overlap`` — доля основ названия лидера, встретившихся в запросе;
        * ``bm25_ratio`` — во сколько раз BM25 лидера выше второго места BM25;
        * ``strong`` — всё сразу: можно брать семейство без LLM-1.

        Без векторов ``strong`` строже: название лидера целиком в запросе и отрыв BM25 в
        :data:`STRONG_BM25_RATIO` раз — одна лексика иначе путает «Кресло офисное» и
        «Кресло компьютерное детское».
        """
        features: dict = {
            "has_vectors": False,
            "top1_id": None,
            "top1_cosine": None,
            "top2_cosine": None,
            "cosine_margin": None,
            "bm25_top1_id": None,
            "vector_top1_id": None,
            "agree": False,
            "name_overlap": None,
            "bm25_ratio": None,
            "strong": False,
        }
        if not hits:
            return features
        top = hits[0]
        info = self._lookup(top)
        # Признаки зависят от того, участвовал ли вектор в ЭТОМ поиске (vectors=False, сбой).
        used_vectors = info.used_vectors if info else any(h.cosine is not None for h in hits)
        features["has_vectors"] = used_vectors
        features["top1_id"] = top.family_id
        features["bm25_top1_id"] = next((h.family_id for h in hits if h.bm25_rank == 1), None)
        features["vector_top1_id"] = next((h.family_id for h in hits if h.vector_rank == 1), None)
        # Соперники лидера для отрыва. Не соперники: более общий тип с тем же названием
        # («Брюки» при лидере «Брюки спортивные») — тот же товар, названный менее точно, и более
        # узкий, чьих отличительных слов в запросе нет («… детские» при «брюки спортивные
        # женские»): пользователь назвал бы их.
        top_name = self._name_of(top.family_id)
        query_terms = set(analyze(clean_query(text, self.brands), self.prefix)) if text else set()

        def rival(hit: FamilyHit) -> bool:
            name = self._name_of(hit.family_id)
            if not top_name:
                return True
            if name < top_name:
                return False
            return not (top_name < name and not (name - top_name) & query_terms)

        others = [hit.cosine for hit in hits[1:] if hit.cosine is not None and rival(hit)]
        if top.cosine is not None:
            features["top1_cosine"] = top.cosine
            features["top2_cosine"] = max(others) if others else None
            features["cosine_margin"] = round(top.cosine - max(others), 4) if others else None
        features["agree"] = top.bm25_rank == 1 and (top.vector_rank == 1 or not used_vectors)

        if text is not None:
            terms: Iterable[str] | None = analyze(clean_query(text, self.brands), self.prefix)
        else:
            terms = info.terms if info else None
        if terms is not None:
            features["name_overlap"] = round(self.name_overlap(terms, top.family_id), 3)
        if info is not None:
            scores = sorted(info.bm25_scores.values(), reverse=True)
            if scores:
                second = scores[1] if len(scores) > 1 else 0.0
                features["bm25_ratio"] = round(scores[0] / second, 3) if second else math.inf

        features["strong"] = self._strong(features)
        return features

    def _strong(self, f: dict) -> bool:
        if not f["agree"] or f["name_overlap"] is None:
            return False
        if f["has_vectors"]:
            margin = f["cosine_margin"]
            return (
                f["name_overlap"] >= STRONG_NAME_OVERLAP
                and margin is not None
                and margin >= STRONG_COSINE_MARGIN
            )
        ratio = f["bm25_ratio"]
        return (
            f["name_overlap"] >= STRONG_NAME_OVERLAP
            and ratio is not None
            and ratio >= STRONG_BM25_RATIO
        )

    def _lookup(self, hit: FamilyHit) -> _SearchInfo | None:
        with self._lock:
            entry = self._info.get(id(hit))
        # Сверка тождества: id() переиспользуется после сборки мусора.
        if entry is not None and entry[0] is hit:
            return entry[1]
        return None


def _model_id(name: str) -> str:
    folded = re.sub(r"[^0-9a-z.]", "", name.casefold().rsplit("/", 1)[-1])
    return folded.replace("embedding", "emb")


def same_model(a: str, b: str) -> bool:
    """Одна ли модель: имена каталогов бывают разными для одних весов.

    «qwen3-emb-0.6b» (локальная копия), «qwen3-embedding-0.6b» (образ) и
    «Qwen/Qwen3-Embedding-0.6B» — одна модель. Каталог-снимок кэша HuggingFace назван хэшем —
    по нему модель не определить, такое имя не сравнивается.
    """
    if any(re.fullmatch(r"[0-9a-f]{40}", name) for name in (a, b)):
        return True
    return _model_id(a) == _model_id(b)


def _lexically_unsure(bm25: np.ndarray, order: list[int]) -> bool:
    """Нужен ли вектор: BM25 нашёл мало или лидер почти не отрывается от второго места."""
    if len(order) < AUTO_MIN_HITS:
        return True
    top, second = float(bm25[order[0]]), float(bm25[order[1]])
    return second > 0 and top < AUTO_RATIO * second


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def load_retriever(kb: KnowledgeBase, directory: Path | None = None, embedder=None) -> Retriever:
    """Поиск процесса: BM25 всегда, векторы — если посчитаны и эмбеддер доступен."""
    from apps.ste import config
    from apps.ste.kb_build.embed import load_vectors

    directory = directory or config.KB_DIR
    vectors = load_vectors(directory)
    if vectors is None:
        logger.info("Векторов семейств нет в %s — поиск только лексический", directory)
        return Retriever(kb)
    if embedder is None:
        from apps.ste.infrastructure.embedder import get_embedder

        embedder = get_embedder()
    if not embedder.enabled:
        logger.info("Эмбеддер выключен или нет весов — поиск только лексический")
        return Retriever(kb)
    return Retriever(kb, embedder, vectors)
