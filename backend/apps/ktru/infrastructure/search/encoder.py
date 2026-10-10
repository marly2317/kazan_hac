"""Наименование товара — в вектор.

Семантическая часть поиска нужна ровно для одного: случая, когда у запроса и наименования
позиции **нет общих букв**. «Болгарка» и «Шлифмашина» — такой случай, и ни стеммер, ни
триграммы его не берут. Словарь синонимов берёт, но только то, что в него внесли; вектор
отвечает и на то, чего в словаре нет.

## Почему не внутри OpenSearch

Движок умеет считать эмбеддинги сам — плагин `ml-commons` принимает предобученную модель и
`text_embedding`-процессор добавляет вектор при индексации. Это сняло бы с нас и эту
ответственность, но стоило бы примерно четырёх гигабайт памяти узлу и привязало бы выбор
модели к тому, что умеет принимать движок. Здесь модель весит 120 МБ, и считать вектор
наименования — это единицы миллисекунд на CPU, поэтому обмен невыгоден.

OpenSearch при этом остаётся ответственным за то, что действительно трудно: HNSW-граф,
приближённый поиск соседей, слияние с лексикой. Наша часть — один вызов `encode`.

## Чем это не угрожает

Плотный поиск всегда возвращает правдоподобных ближайших соседей — и на бессмысленный запрос
тоже. Поэтому вектор участвует **только в отборе** кандидатов, а решение «не нашлось»
принимается после него и по другому признаку: по словам наименования
(`apps.autofill.domain.matching`). Семантика добавлена *в дополнение* к полноте, а не вместо
порога уверенности, — иначе она стоила бы корзины `absent`, которая в замере даёт 5/5.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)

#: Размерность, объявляемая индексу, когда энкодер выключен.
#:
#: Поле `knn_vector` объявляется всегда: индекс с полем, но без векторов, ищется лексикой и
#: доиндексируется потом, а индекс без поля пришлось бы пересоздавать. Значение — размерность
#: rubert-tiny2, то есть то, что окажется там при включении.
FALLBACK_DIMENSION = 312


class Encoder:
    """Обёртка над моделью с ленивой загрузкой.

    Ленивой намеренно: модель занимает память в каждом процессе gunicorn, и платить за неё
    при импорте модуля — в том числе в `manage.py migrate` и в тестах — значило бы платить
    всегда. Первый вызов :meth:`encode` стоит около секунды, дальше — миллисекунды.
    """

    def __init__(self, path: str, enabled: bool) -> None:
        self._path = path
        self._enabled = enabled
        self._model = None
        self._broken = False

    @property
    def enabled(self) -> bool:
        """Энкодер включён настройкой и до сих пор не падал."""
        return self._enabled and not self._broken

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Загрузка энкодера: %s", self._path)
            self._model = SentenceTransformer(self._path)
        return self._model

    @property
    def dimension(self) -> int:
        """Размерность вектора — её объявляют индексу при создании."""
        if not self.enabled:
            return FALLBACK_DIMENSION
        try:
            return int(self._load().get_sentence_embedding_dimension())
        except Exception as error:  # noqa: BLE001
            logger.warning("Энкодер недоступен (%s) — поиск остаётся лексическим", error)
            self._broken = True
            return FALLBACK_DIMENSION

    def encode(self, texts: list[str]) -> list[list[float]] | None:
        """Векторы для списка строк, либо ``None``, когда энкодера нет.

        ``None``, а не исключение: отсутствие семантики делает поиск беднее, но не ломает его,
        и падать из-за этого на запросе пользователя было бы хуже, чем ответить лексикой.
        Пустой список строк — тоже ``None``: векторов ноль, и вызывающей стороне нечего
        добавлять в запрос.
        """
        if not self.enabled or not texts:
            return None
        try:
            model = self._load()
            # `normalize_embeddings` — чтобы косинус считался по уже нормированным векторам:
            # HNSW с `cosinesimil` нормирует сам, но вектор запроса и вектор документа должны
            # приходить одинаковыми, иначе ближайшие соседи зависят от длины наименования.
            vectors = model.encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            )
            return [[float(value) for value in vector] for vector in vectors]
        except Exception as error:  # noqa: BLE001
            logger.warning("Энкодер отказал (%s) — поиск остаётся лексическим", error)
            self._broken = True
            return None

    def encode_one(self, text: str) -> list[float] | None:
        vectors = self.encode([text])
        return vectors[0] if vectors else None


@lru_cache(maxsize=1)
def encoder() -> Encoder:
    """Энкодер процесса. Один на процесс: модель в памяти — самое дорогое, что здесь есть."""
    return Encoder(settings.EMBEDDING_MODEL_PATH, settings.EMBEDDING_ENABLED)
