"""Реранкер Qwen3-Reranker-0.6B: переупорядочивает кандидатов поиска (архитектура §4.0–4.1).

Кросс-энкодер видит запрос и карточку семейства **вместе** и выдаёт вероятность того, что
карточка отвечает на запрос, — откалиброванную оценку в [0, 1], в отличие от места в RRF или
самооценки LLM. Формат входа — как в карточке модели: системная инструкция, `<Instruct>`,
`<Query>`, `<Document>`, ответ «yes»/«no»; оценка — P(yes) на последнем токене.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from functools import lru_cache

from apps.ste import config

logger = logging.getLogger(__name__)

INSTRUCTION = (
    "Определи, относится ли описание товара от поставщика к этому типу товара "
    "из каталога КТРУ (одежда, обувь, сумки, мебель, инструменты)"
)
_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query "
    'and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
    "<|im_start|>user\n"
)
_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
MAX_LENGTH = 512
BATCH_SIZE = 8


class Reranker:
    def __init__(self, path: str, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._model = None
        self._tokenizer = None
        self._ids: tuple[int, int] | None = None
        self._lock = threading.Lock()
        self._broken = False
        #: ``cuda`` или ``cpu`` — после загрузки модели; для трассировки.
        self.device: str | None = None

    @property
    def available(self) -> bool:
        return self.enabled and not self._broken

    def _load(self) -> bool:
        if self._model is not None:
            return True
        if not self.available:
            return False
        with self._lock:
            if self._model is not None:
                return True
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer

                tokenizer = AutoTokenizer.from_pretrained(self.path, padding_side="left")
                device = "cuda" if torch.cuda.is_available() else "cpu"
                # bf16 на CPU без аппаратной поддержки в разы медленнее fp32 — проверено.
                dtype = torch.float16 if device == "cuda" else torch.float32
                model = AutoModelForCausalLM.from_pretrained(self.path, dtype=dtype).eval()
                self._model = model.to(device)
                self.device = device
                self._tokenizer = tokenizer
                self._ids = (
                    tokenizer.convert_tokens_to_ids("no"),
                    tokenizer.convert_tokens_to_ids("yes"),
                )
                logger.info("Реранкер загружен: %s (%s)", self.path, device)
            except Exception as error:  # noqa: BLE001 — без реранкера порядок остаётся поиска
                logger.warning("Реранкер недоступен (%s): %s", self.path, error)
                self._broken = True
                return False
        return True

    def score(
        self, query: str, documents: Sequence[str], *, logits: bool = False
    ) -> list[float] | None:
        """P(yes) для каждой пары (запрос, документ); ``None`` — реранкер недоступен.

        ``logits=True`` — разность логитов yes−no: она не насыщается у единицы, и порядок
        между несколькими «почти наверняка да» остаётся осмысленным.
        """
        if not documents or not self._load():
            return None
        import torch

        texts = [
            f"{_PREFIX}<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: {document}{_SUFFIX}"
            for document in documents
        ]
        no_id, yes_id = self._ids
        scores: list[float] = []
        try:
            with torch.inference_mode():
                for start in range(0, len(texts), BATCH_SIZE):
                    batch = self._tokenizer(
                        texts[start : start + BATCH_SIZE],
                        padding=True,
                        truncation=True,
                        max_length=MAX_LENGTH,
                        return_tensors="pt",
                    ).to(self._model.device)
                    out = self._model(**batch).logits[:, -1, :]
                    margin = (out[:, yes_id] - out[:, no_id]).float()
                    scores += (margin if logits else torch.sigmoid(margin)).tolist()
        except Exception as error:  # noqa: BLE001
            logger.warning("Реранкер отказал: %s", error)
            return None
        return scores


@lru_cache(maxsize=1)
def get_reranker() -> Reranker:
    return Reranker(config.RERANKER_PATH, config.RERANKER_ENABLED)
