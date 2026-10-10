"""Эмбеддер карточек семейств и запросов поставщика (архитектура §3.9).

Вектор нужен поиску ровно там, где у запроса и карточки КТРУ нет общих основ: «болгарка» и
«Шлифмашина», «пуховик» и «Куртка зимняя». Модель — Qwen3-Embedding-0.6B на CPU, локально:
никаких внешних вызовов, а запрос из 5–15 слов кодируется за десятки миллисекунд.

Асимметрия Qwen3-Embedding: запрос кодируется **с инструкцией** (``Instruct: …\\nQuery: …``),
документ — без неё. Перепутать — потерять заметную долю качества, поэтому методов два:
:meth:`Embedder.encode_queries` и :meth:`Embedder.encode_docs`.

Отсутствие модели — штатная ситуация (CI, стенд без весов): методы возвращают ``None``, поиск
остаётся лексическим. Исключение наружу не выходит: без семантики поиск беднее, но не сломан.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

import numpy as np

from apps.ste import config

logger = logging.getLogger(__name__)

#: Длинее карточки семейства не бывают (≈ 1100 символов ≈ 400 токенов); обрезка страхует от
#: квадратичного по длине внимания на случайно огромном тексте.
MAX_SEQ_LENGTH = 512
BATCH_SIZE = 16


class Embedder:
    """Ленивая обёртка над SentenceTransformer.

    Ленивая намеренно: 0.6B-модель — это ~1,2 ГБ памяти в каждом процессе, и платить за неё
    при ``manage.py migrate`` или в тестах, которым вектор не нужен, незачем. Первый вызов
    стоит несколько секунд (загрузка весов), дальше — миллисекунды.
    """

    def __init__(
        self,
        path: str | Path | None,
        enabled: bool = True,
        *,
        query_instruction: str = config.EMBEDDER_QUERY_INSTRUCTION,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self.path = Path(path) if path else None
        self._enabled = enabled
        self.query_instruction = query_instruction
        self.batch_size = batch_size
        self._model = None
        self._broken = False
        # Django runserver и gunicorn с потоками: два первых запроса не должны грузить веса дважды.
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        """Имя модели для метаданных векторов: по нему ловим векторы от другой модели."""
        return self.path.name if self.path else ""

    @property
    def enabled(self) -> bool:
        """Включён настройкой, веса на месте и загрузка до сих пор не падала."""
        return (
            self._enabled
            and not self._broken
            and self.path is not None
            and (self._model is not None or self.path.exists())
        )

    @property
    def available(self) -> bool:
        """Модель реально загружается (загружает её, если ещё не загружена)."""
        return self._load() is not None

    @property
    def dimension(self) -> int | None:
        model = self._load()
        if model is None:
            return None
        return int(model.get_sentence_embedding_dimension())

    def _load(self):
        if not self.enabled:
            return None
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer

                logger.info("Загрузка эмбеддера: %s", self.path)
                model = SentenceTransformer(str(self.path), device="cpu")
                model.max_seq_length = min(model.max_seq_length or MAX_SEQ_LENGTH, MAX_SEQ_LENGTH)
                self._model = model
            except Exception as error:  # noqa: BLE001
                logger.warning("Эмбеддер недоступен (%s) — поиск только лексический", error)
                self._broken = True
                return None
        return self._model

    def _encode(self, texts: Sequence[str]) -> np.ndarray | None:
        if not texts:
            return None
        model = self._load()
        if model is None:
            return None
        try:
            vectors = model.encode(
                list(texts),
                batch_size=self.batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as error:  # noqa: BLE001
            logger.warning("Эмбеддер отказал (%s) — поиск только лексический", error)
            self._broken = True
            return None
        return np.ascontiguousarray(vectors, dtype=np.float32)

    def encode_queries(self, texts: Sequence[str]) -> np.ndarray | None:
        """Запросы поставщика — с инструкцией задачи. Матрица ``n × d``, строки нормированы."""
        return self._encode([self.query_instruction + text for text in texts])

    def encode_docs(self, texts: Sequence[str]) -> np.ndarray | None:
        """Документы (карточки семейств) — без инструкции. Матрица ``n × d``, нормирована."""
        return self._encode(texts)


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Эмбеддер процесса: один на процесс, модель в памяти — самое дорогое, что здесь есть."""
    return Embedder(config.EMBEDDER_PATH, config.EMBEDDER_ENABLED)
