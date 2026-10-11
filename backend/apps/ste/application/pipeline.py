"""Онлайн-пайплайн подбора КТРУ (архитектура §2, §4, §6.4).

Публичные функции — ровно как в архитектуре §6.4: ``analyze``, ``answer``, ``answer_text``,
``choose_family``, ``choose_leaf``, ``edit``, плюс ``preview`` для подсказок по мере набора.

Устройство: каждая функция **меняет состояние сессии** (то, что пришло от пользователя и от
LLM), а ответ всегда строится одной детерминированной функцией :meth:`Pipeline.evaluate` из
состояния: сужение, вопрос, карточка, сигналы ошибок. Поэтому ответ кнопкой пересчитывается
без LLM (§4.8), а повтор запроса даёт тот же ответ.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from apps.ste import config
from apps.ste.domain.kb import Family, FamilyChar, KnowledgeBase
from apps.ste.domain.model import (
    Confidence,
    ExtractedValue,
    FamilyChoice,
    FamilyHit,
    Source,
    Status,
    TypoFix,
)
from apps.ste.domain.normalize import normalize_value, option_value, split_number_unit
from apps.ste.domain.values import format_number

logger = logging.getLogger(__name__)

UNKNOWN_ANSWER = "__unknown__"

#: Сколько семейств-кандидатов LLM-1 доводим до LLM-2 (архитектура §4.1).
MAX_FAMILIES = 3


# --- состояние сессии --------------------------------------------------------------------


@dataclass
class State:
    original: str = ""
    text: str = ""
    fixes: list[dict] = field(default_factory=list)
    preview: list[dict] = field(default_factory=list)
    #: Кандидаты-семейства после правил решения §4.1: FamilyChoice как dict.
    candidates: list[dict] = field(default_factory=list)
    #: Как выбрано семейство: llm | fast | fallback | user; not_found; reason.
    selection: dict = field(default_factory=dict)
    #: family_id → char_key → ExtractedValue (dict).
    extracted: dict[str, dict[str, dict]] = field(default_factory=dict)
    #: family_id → char_key → NormalizedValue (dict).
    values: dict[str, dict[str, dict]] = field(default_factory=dict)
    extra: dict[str, list[dict]] = field(default_factory=dict)
    duplicates: dict[str, list[dict]] = field(default_factory=dict)
    brand: str | None = None
    model: str | None = None
    skipped: list[str] = field(default_factory=list)
    chosen_leaf: str | None = None
    llm_note: str | None = None
    timings: dict[str, int] = field(default_factory=dict)
    #: Шаги последнего запроса по порядку — для блока «Как отработала система».
    steps: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> State:
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in known})


def _ev_from(data: dict) -> ExtractedValue:
    return ExtractedValue(
        char_key=data["char_key"],
        status=Status(data["status"]),
        value=data.get("value"),
        unit=data.get("unit"),
        quote=data.get("quote"),
        quote_found=bool(data.get("quote_found")),
        source=Source(data.get("source", "text")),
        confidence=Confidence(data.get("confidence", "weak")),
    )


def _nv_from(data: dict):
    from apps.ste.domain.model import NormalizedValue

    payload = dict(data)
    payload["confidence"] = Confidence(payload.get("confidence", "weak"))
    payload["source"] = Source(payload.get("source", "text"))
    known = NormalizedValue.__dataclass_fields__
    return NormalizedValue(**{key: value for key, value in payload.items() if key in known})


_STAGE_NOTES = {
    "done": "итог: код определён",
    "question": "итог: нужен уточняющий вопрос",
    "not_found": "итог: подходящего КТРУ нет",
    "conflict": "итог: противоречие с КТРУ",
}


def _outcome_note(result: dict) -> str:
    note = _STAGE_NOTES.get(result.get("stage", ""), f"итог: {result.get('stage', '')}")
    if result.get("final_code"):
        note += f" {result['final_code']}"
    elif result.get("leaves_count"):
        note += f"; осталось кодов: {result['leaves_count']}"
    return note


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


class _RecordedLlm:
    """Обёртка клиента LLM: каждый вызов записывается в шаги запроса (модель, время, кэш)."""

    def __init__(self, llm: Any, steps: list[dict], lock: threading.Lock, label: str) -> None:
        self._llm = llm
        self._label = label
        self._steps = steps
        self._lock = lock

    def complete_json(self, messages, schema, name, *args, **kwargs):
        started = time.perf_counter()
        try:
            response = self._llm.complete_json(messages, schema, name, *args, **kwargs)
        except Exception as error:
            with self._lock:
                self._steps.append(
                    {
                        "step": self._label,
                        "model": getattr(self._llm, "model", None),
                        "ms": _ms(started),
                        "note": f"ошибка: {error}"[:200],
                    }
                )
            raise
        usage = getattr(response, "usage", {}) or {}
        tokens = usage.get("total_tokens")
        note = (
            "ответ из кэша, запроса к модели не было"
            if getattr(response, "cached", False)
            else (f"{tokens} токенов" if tokens else "запрос к модели")
        )
        with self._lock:
            self._steps.append(
                {
                    "step": self._label,
                    "model": getattr(response, "model", None),
                    "ms": _ms(started),
                    "note": note,
                }
            )
        return response

    def __getattr__(self, name: str):
        return getattr(self._llm, name)


# --- зависимости ---------------------------------------------------------------------------


@dataclass
class Deps:
    kb: KnowledgeBase
    corrector: Any  # .correct(text) -> TypoResult
    retriever: Any  # .search(text, k) -> list[FamilyHit]; .confidence(hits) -> dict
    llm: Any | None  # LlmClient | None
    store: Any  # SessionStore
    reranker: Any | None = None  # Reranker | None


_STEPS_LOCK = threading.Lock()


class PipelineError(Exception):
    """Ошибка запроса пользователя: нет сессии, неизвестная характеристика и т. п."""


class Pipeline:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.kb = deps.kb

    # === публичные функции (архитектура §6.4) ===============================================

    def preview(self, text: str) -> dict:
        start = time.perf_counter()
        corrected = self._correct(text)[0] if text.strip() else text
        hits = self._search(corrected, k=5) if corrected.strip() else []
        return {
            "hits": [self._hit_json(hit) for hit in hits],
            "timings_ms": {"total": _ms(start)},
        }

    def analyze(self, text: str, session_id: str | None = None) -> dict:
        total = time.perf_counter()
        state = State(original=text)

        start = time.perf_counter()
        state.text, fixes = self._correct(text)
        state.text = join_compound_lengths(state.text)
        state.fixes = [asdict(fix) for fix in fixes]
        state.timings["typos"] = _ms(start)
        self._step(
            state,
            "Опечатки и раскладка",
            "SymSpell + раскладка клавиатуры по словарю КТРУ (без нейросети)",
            state.timings["typos"],
            f"исправлено слов: {len(fixes)}" if fixes else "исправлять нечего",
        )

        if not state.text.strip():
            state.selection = {"not_found": True, "mode": "empty", "reason": "Пустое описание"}
            return self._finish(state, session_id, total)

        start = time.perf_counter()
        hits = self._search(state.text, k=max(config.FAMILY_TOP_K, 10), state=state)
        state.preview = [self._hit_json(hit) for hit in hits[:5]]
        state.timings["preview"] = _ms(start)

        if self._multiple_products(state, hits):
            # Несколько товаров в одной позиции: сначала пользователь выбирает, какой оформляем;
            # извлекать характеристики сразу для всех — лишние вызовы LLM.
            return self._finish(state, session_id, total)

        self._select_family(state, hits)

        start = time.perf_counter()
        self._extract_all(
            state, [choice["family_id"] for choice in state.candidates[:MAX_FAMILIES]]
        )
        state.timings["llm2"] = _ms(start)

        return self._finish(state, session_id, total)

    def answer(self, session_id: str, char_key: str, value: str) -> dict:
        total = time.perf_counter()
        state = self._load(session_id)
        state.llm_note = None
        family = self._focus_family(state)
        if family is None:
            raise PipelineError("Семейство ещё не определено — сначала выберите тип товара.")
        if value == UNKNOWN_ANSWER:
            if char_key not in state.skipped:
                state.skipped.append(char_key)
            return self._finish(state, session_id, total)
        spec = family.chars.get(char_key)
        if spec is None:
            raise PipelineError(f"У семейства «{family.name}» нет характеристики «{char_key}».")
        nv = self._value_from_option(spec, value, Source.ANSWER)
        state.values.setdefault(family.id, {})[char_key] = asdict(nv)
        state.extracted.setdefault(family.id, {})[char_key] = asdict(
            ExtractedValue(
                char_key=char_key,
                status=Status.SPECIFIED,
                value=value,
                quote=value,
                quote_found=True,
                source=Source.ANSWER,
                confidence=Confidence.CONFIRMED,
            )
        )
        if char_key in state.skipped:
            state.skipped.remove(char_key)
        return self._finish(state, session_id, total)

    def answer_text(self, session_id: str, char_key: str, text: str) -> dict:
        total = time.perf_counter()
        state = self._load(session_id)
        family = self._focus_family(state)
        if family is None:
            raise PipelineError("Семейство ещё не определено — сначала выберите тип товара.")
        if char_key not in family.chars:
            raise PipelineError(f"У семейства «{family.name}» нет характеристики «{char_key}».")
        ev = None
        llm_answered = False
        if self.deps.llm is not None:
            try:
                from apps.ste.application.llm_steps import extract_one

                ev = extract_one(
                    self._llm(state, "mini-LLM-2 · разбор ответа"), text, family, char_key
                )
                llm_answered = True
            except Exception as error:  # noqa: BLE001 — деградация вместо отказа
                logger.warning("mini-LLM-2 недоступна: %s", error)
                state.llm_note = _llm_note(error)
        if ev is None and llm_answered:
            # Модель прочитала ответ и значения в нём не нашла («не знаю», «любой») — это
            # пропуск вопроса, а не значение: иначе текст ответа стал бы «недопустимым
            # значением» в списке ошибок.
            if char_key not in state.skipped:
                state.skipped.append(char_key)
            return self._finish(state, session_id, total)
        if ev is None:
            ev = self._rule_value(family.chars[char_key], text, Source.ANSWER)
        ev.source = Source.ANSWER
        self._store_value(state, family, ev)
        return self._finish(state, session_id, total)

    def edit(self, session_id: str, char_key: str, value: str) -> dict:
        total = time.perf_counter()
        state = self._load(session_id)
        state.llm_note = None
        family = self._focus_family(state)
        if family is None or char_key not in family.chars:
            raise PipelineError("Неизвестная характеристика.")
        ev = self._rule_value(family.chars[char_key], value, Source.EDIT)
        ev.confidence = Confidence.CONFIRMED
        self._store_value(state, family, ev, force_confirmed=True)
        if char_key in state.skipped:
            state.skipped.remove(char_key)
        return self._finish(state, session_id, total)

    def set_extra(self, session_id: str, name: str, value: str) -> dict:
        """Дополнительная характеристика от пользователя — например, из подсказки «часто
        указывают в СТЕ этого типа». Пустое значение удаляет характеристику."""
        from apps.ste.domain.extra_filter import same_name

        total = time.perf_counter()
        state = self._load(session_id)
        state.llm_note = None
        family = self._focus_family(state)
        name = name.strip()
        if family is None or not name:
            raise PipelineError("Сначала определите тип товара.")
        items = [
            item
            for item in state.extra.get(family.id, [])
            if not same_name(str(item.get("name") or ""), name)
        ]
        value = value.strip()
        if value:
            number, unit = split_number_unit(value)
            items.append(
                {
                    "name": name[:1].upper() + name[1:],
                    "value": format_number(number) if number is not None and unit else value,
                    "unit": unit if number is not None else None,
                    "quote": None,
                    "source": "user",
                }
            )
        state.extra[family.id] = items
        return self._finish(state, session_id, total)

    def choose_family(self, session_id: str, family_id: str) -> dict:
        total = time.perf_counter()
        state = self._load(session_id)
        family = self.kb.families.get(family_id) or self._by_short(family_id)
        if family is None:
            raise PipelineError("Неизвестное семейство КТРУ.")
        state.candidates = [
            asdict(FamilyChoice(family.id, 1.0, "выбрано пользователем", fixed_by_user=True))
        ]
        state.selection = {"not_found": False, "mode": "user", "reason": "выбрано пользователем"}
        state.chosen_leaf = None
        if family.id not in state.extracted:
            start = time.perf_counter()
            self._extract(state, family.id)
            state.timings["llm2"] = _ms(start)
        return self._finish(state, session_id, total)

    def choose_leaf(self, session_id: str, code: str) -> dict:
        total = time.perf_counter()
        state = self._load(session_id)
        state.llm_note = None
        leaf = self.kb.leaves.get(code)
        if leaf is None:
            raise PipelineError("Неизвестный код КТРУ.")
        if not any(choice["family_id"] == leaf.family_id for choice in state.candidates):
            state.candidates = [
                asdict(FamilyChoice(leaf.family_id, 1.0, "выбрано пользователем", True))
            ]
        state.chosen_leaf = code
        return self._finish(state, session_id, total)

    # === шаги ================================================================================

    def _llm(self, state: State, label: str):
        """Клиент LLM, записывающий вызовы в шаги запроса; ``None`` — LLM не настроена."""
        if self.deps.llm is None:
            return None
        return _RecordedLlm(self.deps.llm, state.steps, _STEPS_LOCK, label)

    @staticmethod
    def _step(state: State, step: str, model: str | None, ms: int, note: str = "") -> None:
        with _STEPS_LOCK:
            state.steps.append({"step": step, "model": model, "ms": ms, "note": note})

    def _correct(self, text: str) -> tuple[str, list[TypoFix]]:
        try:
            result = self.deps.corrector.correct(text)
            return result.corrected, list(result.fixes)
        except Exception as error:  # noqa: BLE001 — корректор не вправе уронить подбор
            logger.warning("Корректор отказал: %s", error)
            return text, []

    def _search(self, text: str, k: int, state: State | None = None) -> list[FamilyHit]:
        retriever = self.deps.retriever
        try:
            if state is None or not hasattr(retriever, "search_timed"):
                return list(retriever.search(text, k=k))
            hits, timings = retriever.search_timed(text, k=k)
        except Exception as error:  # noqa: BLE001
            logger.warning("Поиск отказал: %s", error)
            return []
        families = len(self.kb.selectable())
        self._step(
            state,
            "Лексический поиск BM25",
            "BM25 со стеммингом Snowball (без нейросети)",
            int(timings.get("clean", 0) + timings.get("bm25", 0)),
            f"по карточкам {families} семейств-родителей",
        )
        embedder = getattr(retriever, "embedder", None)
        vectors_on = "vector" in timings and getattr(retriever, "has_vectors", False)
        self._step(
            state,
            "Векторный поиск",
            getattr(embedder, "model_name", None) or "Qwen3-Embedding-0.6B",
            int(timings.get("vector", 0)),
            "вектор запроса · косинус к векторам семейств"
            if vectors_on
            else "выключен — только BM25",
        )
        self._step(
            state,
            "Слияние BM25 + векторы (RRF)",
            "формула, без нейросети",
            int(timings.get("fuse", 0)),
            f"топ-{len(hits)}: " + ", ".join(hit.name for hit in hits[:3]) if hits else "пусто",
        )
        return list(hits)

    def _hit_json(self, hit: FamilyHit) -> dict:
        return {
            "family_id": hit.family_id,
            "short_id": hit.short_id,
            "name": hit.name,
            "score": round(float(hit.score), 4),
        }

    def _by_short(self, short_id: str) -> Family | None:
        return self.kb.by_short_id(short_id)

    def _rerank(self, text: str, hits: list[FamilyHit]) -> list[tuple[FamilyHit, float]] | None:
        """Qwen3-Reranker по топу поиска: (кандидат, P(yes)) по убыванию; ``None`` — нет модели."""
        reranker = self.deps.reranker
        if reranker is None or not hits:
            return None
        from apps.ste.domain.cards import short_card

        top = hits[: config.RERANK_TOP]
        documents = [short_card(self.kb.families[hit.family_id]).split("] ", 1)[-1] for hit in top]
        try:
            margins = reranker.score(text, documents, logits=True)
        except Exception as error:  # noqa: BLE001
            logger.warning("Реранкер отказал: %s", error)
            return None
        if margins is None:
            return None
        # Порядок — слияние мест поиска и реранкера (RRF): на реальных наименованиях СТЕ
        # реранкер в одиночку хуже поиска на первом месте (83% против 89%), а слияние — 89% и
        # лучше в топ-3 на демо-сценариях. Оценка реранкера — для уверенности и «не найдено».
        by_margin = sorted(range(len(top)), key=lambda i: -margins[i])
        rerank_pos = {index: place for place, index in enumerate(by_margin, start=1)}
        order = sorted(range(len(top)), key=lambda i: -(1 / (61 + i) + 1 / (60 + rerank_pos[i])))
        return [(top[i], 1 / (1 + math.exp(-margins[i]))) for i in order]

    def _select_family(self, state: State, hits: list[FamilyHit]) -> None:
        """Реранкер → (уверен: сразу) → LLM-1 по его топу; без LLM — по реранкеру/поиску."""
        start = time.perf_counter()
        ranked = self._rerank(state.text, hits)
        if ranked is None and hits:
            self._step(
                state,
                "Реранкер",
                "Qwen3-Reranker-0.6B",
                _ms(start),
                "выключен (STE_RERANKER_ENABLED=0)"
                if self.deps.reranker is None
                else "не сработал (нет весов или ошибка, см. лог)",
            )
        if ranked is not None:
            state.timings["rerank"] = _ms(start)
            device = getattr(self.deps.reranker, "device", None)
            self._step(
                state,
                "Реранкер",
                "Qwen3-Reranker-0.6B" + (f" ({device})" if device else ""),
                state.timings["rerank"],
                f"оценил {min(len(hits), config.RERANK_TOP)} карточек; лидер "
                f"«{ranked[0][0].name}» P={ranked[0][1]:.2f}",
            )
            state.preview = [
                {**self._hit_json(hit), "score": round(prob, 4)} for hit, prob in ranked[:5]
            ]
            (top, p1) = ranked[0]
            p2 = ranked[1][1] if len(ranked) > 1 else 0.0
            if p1 >= config.RERANK_CONFIDENT and p1 - p2 >= config.RERANK_MARGIN:
                self._step(
                    state,
                    "LLM-1 · выбор семейства",
                    None,
                    0,
                    f"не вызывалась: реранкер уверен (P ≥ {config.RERANK_CONFIDENT} "
                    f"и отрыв ≥ {config.RERANK_MARGIN})",
                )
                state.candidates = [asdict(FamilyChoice(top.family_id, p1, "реранкер уверен"))]
                state.selection = {"not_found": False, "mode": "reranker", "reason": "реранкер"}
                return
            if self._llm1_enabled(state):
                try:
                    self._select_with_llm(state, [hit for hit, _ in ranked[: config.RERANK_TO_LLM]])
                    self._cross_check(state, ranked)
                    return
                except Exception as error:  # noqa: BLE001
                    logger.warning("LLM-1 недоступна: %s", error)
                    state.llm_note = _llm_note(error)
            if max(prob for _, prob in ranked) < 0.27:
                state.selection = {
                    "not_found": True,
                    "mode": "reranker",
                    "reason": "ни одно семейство КТРУ не подходит по реранкеру",
                }
            elif p1 >= 0.5 and p1 - p2 >= 0.2:
                state.candidates = [asdict(FamilyChoice(top.family_id, p1, "реранкер"))]
                state.selection = {"not_found": False, "mode": "reranker", "reason": "реранкер"}
            else:
                state.candidates = [
                    asdict(FamilyChoice(hit.family_id, prob, "реранкер"))
                    for hit, prob in ranked[:3]
                ]
                state.selection = {"not_found": False, "mode": "reranker", "reason": "реранкер"}
            return

        fast = self._fast_path(hits, state.text)
        if fast is not None:
            state.candidates = [asdict(fast)]
            state.selection = {"not_found": False, "mode": "fast", "reason": fast.reason}
            return

        if self._llm1_enabled(state):
            try:
                self._select_with_llm(state, hits)
                return
            except Exception as error:  # noqa: BLE001
                logger.warning("LLM-1 недоступна: %s", error)
                state.llm_note = _llm_note(error)

        # Без LLM: уверенный лидер поиска — берём; иначе — вопрос «уточните вид товара».
        if hits:
            top = hits[0]
            features = self._confidence(hits, state.text)
            # Все слова названия лидера есть в запросе («шкаф для одежды деревянный») — тип
            # товара назван прямо, переспрашивать незачем.
            overlap = features.get("name_overlap") or 0
            if (
                len(hits) == 1
                and overlap == 0
                and not features.get("strong")
                and not self._synonym_named(state.text, hits[0].family_id)
            ):
                # Единственный кандидат, ни одного слова его названия в запросе («Отвертка
                # крестовая» → «Кресло офисное» через синоним «крестовина»): без LLM это не
                # решение, а совпадение основ — честнее сказать, что тип не распознан.
                state.selection = {
                    "not_found": True,
                    "mode": "fallback",
                    "reason": "Тип товара не распознан уверенно — уточните описание",
                }
                return
            if features.get("strong") or overlap >= 1.0:
                state.candidates = [
                    asdict(FamilyChoice(top.family_id, 0.8, "лучшее совпадение поиска"))
                ]
            else:
                state.candidates = [
                    asdict(FamilyChoice(hit.family_id, 0.5, "похоже по поиску")) for hit in hits[:5]
                ]
            state.selection = {"not_found": False, "mode": "fallback", "reason": "по поиску"}
        else:
            state.selection = {
                "not_found": True,
                "mode": "fallback",
                "reason": "Поиск не нашёл похожих семейств КТРУ",
            }

    def _synonym_named(self, text: str, family_id: str) -> bool:
        """Назван ли тип синонимом целиком: «болгарка» — да, «крестовая» ≠ «крестовина».

        Поиск сравнивает обрезанные основы («крестов»), поэтому совпадение бывает случайным;
        здесь — полные основы Snowball.
        """
        from apps.ste.application.retrieval import analyze

        family = self.kb.families.get(family_id)
        if family is None:
            return False
        query = set(analyze(text, None))
        words = set(analyze(" ".join((*family.synonyms, *family.keywords, family.name)), None))
        return bool(query & words)

    def _multiple_products(self, state: State, hits: list[FamilyHit]) -> bool:
        """«Стол и стул в одной позиции» → вопрос «какой товар оформляем» + ошибка."""
        from apps.ste.application.text_checks import named_families

        ids = [hit.family_id for hit in hits]
        try:
            ids += [hit.family_id for hit in self.deps.retriever.search(state.text, k=30)]
        except Exception:  # noqa: BLE001 — проверка необязательна
            pass
        named = named_families(state.text, self.kb, ids)
        if not named:
            return False
        state.candidates = [
            asdict(FamilyChoice(family.id, 0.5, "назван в описании")) for family in named[:5]
        ]
        state.selection = {
            "not_found": False,
            "mode": "multiple",
            "reason": "в описании несколько товаров",
        }
        self._step(
            state,
            "Проверка: несколько товаров",
            "правила (без нейросети)",
            0,
            "названы: " + ", ".join(family.name for family in named),
        )
        return True

    def _text_signals(self, state: State, result: dict) -> list[dict]:
        """Проверки описания целиком: минус, несколько товаров, противоречия, нетипичное."""
        from apps.ste.application import text_checks as T
        from apps.ste.domain.model import Severity, Signal, SignalType

        signals = T.negative_value_signals(state.text)
        if state.selection.get("mode") == "multiple":
            families = [
                self.kb.families[choice["family_id"]]
                for choice in state.candidates
                if choice["family_id"] in self.kb.families
            ]
            signals.append(T.multiple_products_signal(families))
        family = self.kb.families.get((result.get("family") or {}).get("id", ""))
        if family is not None and state.selection.get("mode") != "multiple":
            signals += T.contradiction_signals(state.text, family)
            signals += T.unit_signals(state.text, family)
            typical = result.get("typical_extra") or {}
            if typical.get("ste", 0) >= 30:
                for item in typical.get("ours", []):
                    if item["status"] != "rare" or item.get("value") in (None, ""):
                        continue
                    signals.append(
                        Signal(
                            type=SignalType.IRRELEVANT,
                            severity=Severity.WARNING,
                            message=f"«{item['name']}: {item['value']}» — нетипично для "
                            f"«{family.name}»: в {typical['ste']} реальных СТЕ этого типа "
                            "такую характеристику почти не указывают.",
                            suggestion="Проверьте, относится ли она к этому товару; "
                            "если нет — уберите.",
                            char=item["name"],
                            user_value=item["value"],
                            family_id=family.id,
                        )
                    )
        if not signals:
            return []
        from apps.ste.application.result import ResultBuilder

        known = {(item.get("type"), item.get("char")) for item in result.get("signals", [])}
        fresh = [
            signal
            for signal in signals
            if (signal.type.value, signal.char) not in known
            or signal.type != SignalType.CONTRADICTION
        ]
        return ResultBuilder._signals_json(fresh)

    def _nearest_generic(self, text: str, k: int = 5) -> list[Family]:
        """Общие позиции для LLM-1 — только ближайшие по поиску, а не все 81 (~5 тыс. токенов)."""
        try:
            hits = self.deps.retriever.search(text, k=k * 6, include_generic=True)
        except TypeError:  # поиск без общих позиций (заглушки тестов)
            return list(self.kb.generic())[:k]
        generic = [
            self.kb.families[hit.family_id]
            for hit in hits
            if hit.family_id in self.kb.families
            and self.kb.families[hit.family_id].role != "family"
        ]
        return generic[:k]

    @staticmethod
    def _typical_extra(family_id: str) -> list[dict]:
        """Что обычно указывают в реальных СТЕ семейства сверх КТРУ: [{name, share, values}]."""
        from apps.ste.runtime import ste_extra_stats

        try:
            return list((ste_extra_stats().get(family_id) or {}).get("chars") or [])
        except Exception as error:  # noqa: BLE001 — статистика необязательна
            logger.warning("Статистика СТЕ недоступна: %s", error)
            return []

    def _typical_extra_json(self, state: State, family_id: str | None) -> dict | None:
        """Сравнение с практикой портала: КТРУ ↔ извлечённое ↔ что указывают в реальных СТЕ."""
        from apps.ste.domain.ste_compare import compare_with_ste
        from apps.ste.runtime import ste_extra_stats

        family = self.kb.families.get(family_id or "")
        if family is None:
            return None
        return compare_with_ste(
            self._typical_extra(family.id),
            (ste_extra_stats().get(family.id) or {}).get("ste", 0),
            {key: char.name for key, char in family.chars.items()},
            state.extra.get(family.id, []),
        )

    def _llm1_enabled(self, state: State) -> bool:
        if self.deps.llm is None:
            return False
        if not config.LLM_FAMILY_SELECT:
            self._step(
                state,
                "LLM-1 · выбор семейства",
                None,
                0,
                "выключена (STE_LLM_FAMILY_SELECT=0): семейство — по поиску, при "
                "неоднозначности — вопрос",
            )
            return False
        return True

    def _cross_check(self, state: State, ranked: list[tuple[FamilyHit, float]]) -> None:
        """Реранкер уверенно за другое семейство, чем LLM-1, — показать оба (вопрос пользователю).

        Две независимые модели, расходящиеся в выборе, — сигнал не выдавать код молча.
        """
        if state.selection.get("not_found") or not state.candidates:
            return
        top, p1 = ranked[0]
        chosen = {choice["family_id"] for choice in state.candidates}
        if p1 >= 0.7 and top.family_id not in chosen:
            state.candidates.append(asdict(FamilyChoice(top.family_id, p1, "реранкер")))

    def _confidence(self, hits: list[FamilyHit], text: str | None = None) -> dict:
        try:
            return dict(self.deps.retriever.confidence(hits, text))
        except TypeError:
            return dict(self.deps.retriever.confidence(hits))
        except Exception:  # noqa: BLE001
            return {}

    def _fast_path(self, hits: list[FamilyHit], text: str) -> FamilyChoice | None:
        """Быстрый путь без LLM-1: поиск однозначен (архитектура — наше улучшение §4.1).

        Включается только при согласии лексики и вектора и заметном отрыве: экономит вызов LLM
        и секунды ответа ровно там, где LLM-1 ничего бы не добавила.
        """
        if not config_fast_path() or not hits:
            return None
        features = self._confidence(hits, text)
        if features.get("strong"):
            top = hits[0]
            return FamilyChoice(top.family_id, 0.9, "однозначное совпадение по поиску")
        return None

    def _select_with_llm(self, state: State, hits: list[FamilyHit]) -> None:
        """LLM-1 с замером: в трассировке время LLM-1 — только когда она правда вызывалась."""
        start = time.perf_counter()
        try:
            self._llm_family_choice(state, hits)
        finally:
            state.timings["llm1"] = _ms(start)

    def _llm_family_choice(self, state: State, hits: list[FamilyHit]) -> None:
        from apps.ste.application.llm_steps import select_family
        from apps.ste.domain.cards import generic_card, short_card

        if config.FAMILY_SELECT_MODE == "full_list" or not hits:
            families = self.kb.selectable()
        else:
            families = [
                self.kb.families[hit.family_id] for hit in hits if hit.family_id in self.kb.families
            ]
            families = [family for family in families if family.role == "family"][
                : config.FAMILY_TOP_K
            ]
        generic = self._nearest_generic(state.text)
        result = select_family(
            self._llm(state, "LLM-1 · выбор семейства"),
            state.text,
            [short_card(family) for family in families],
            [generic_card(family) for family in generic],
            [family.short_id for family in (*families, *generic)],
        )
        choices: list[FamilyChoice] = []
        for short_id, score, reason in result.candidates:
            # Внутренний номер карточки («семейство F267») пользователю ничего не говорит.
            reason = re.sub(r"\s*\(?\[?\bF\d+\]?\)?", "", reason or "").strip()
            family = self.kb.by_short_id(short_id)
            if family is not None:
                choices.append(FamilyChoice(family.id, float(score), reason or ""))
        choices.sort(key=lambda item: -item.score)

        if result.not_found or not choices:
            state.selection = {
                "not_found": True,
                "mode": "llm",
                "reason": result.not_found_reason or "Тип товара не найден среди семейств КТРУ",
            }
            return

        top = choices[0]
        second = choices[1].score if len(choices) > 1 else 0.0
        if top.score >= config.FAMILY_CONFIDENT and top.score - second >= config.FAMILY_MARGIN:
            kept = [top]
        else:
            kept = [choice for choice in choices if choice.score >= config.FAMILY_MIN][
                :MAX_FAMILIES
            ]
        if not kept:
            state.selection = {
                "not_found": True,
                "mode": "llm",
                "reason": "Уверенность в типе товара ниже порога",
                "weak": [asdict(choice) for choice in choices],
            }
            return
        state.candidates = [asdict(choice) for choice in kept]
        state.selection = {"not_found": False, "mode": "llm", "reason": top.reason}

    def _extract_all(self, state: State, family_ids: list[str]) -> None:
        """LLM-2 по каждому семейству-кандидату — параллельно (архитектура §4.3).

        Клиент потокобезопасен; вызовы разносит его ограничитель частоты. Состояние меняется
        только в основном потоке, после того как все ответы получены.
        """
        if len(family_ids) <= 1 or self.deps.llm is None:
            for family_id in family_ids:
                self._extract(state, family_id)
            return
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(family_ids)) as pool:
            fetched = list(pool.map(lambda fid: self._call_llm2(state, fid), family_ids))
        for family_id, outcome in zip(family_ids, fetched, strict=True):
            self._extract(state, family_id, prefetched=outcome)

    def _call_llm2(self, state: State, family_id: str):
        from apps.ste.application.llm_steps import extract_values

        family = self.kb.families[family_id]
        if not family.chars:
            return None
        try:
            llm = self._llm(state, f"LLM-2 · характеристики «{family.name}»")
            hints = [item["name"] for item in self._typical_extra(family_id)]
            return extract_values(llm, state.text, family, hints or None)
        except Exception as error:  # noqa: BLE001
            return error

    def _extract(self, state: State, family_id: str, prefetched=None) -> None:
        """LLM-2 по схеме семейства (§4.3); без LLM — правила по словарю семейства."""
        family = self.kb.families[family_id]
        extracted: dict[str, ExtractedValue] = {}
        extra: list[dict] = []
        duplicates: list[dict] = []
        if self.deps.llm is not None and family.chars:
            result = prefetched if prefetched is not None else self._call_llm2(state, family_id)
            if isinstance(result, Exception):
                logger.warning("LLM-2 недоступна: %s", result)
                state.llm_note = _llm_note(result)
            elif result is not None:
                extracted = dict(result.values)
                extra = self._unify_extra(list(result.extra or []))
                state.brand = state.brand or result.brand
                state.model = state.model or result.model
                for items in (getattr(result, "duplicates", None) or {}).values():
                    duplicates += [asdict(item) for item in items]
        if not extracted and family.chars:
            from apps.ste.application.rule_extract import extract_by_rules

            start = time.perf_counter()
            extracted = extract_by_rules(state.text, family)
            self._step(
                state,
                f"Извлечение без LLM «{family.name}»",
                "правила по словарю семейства",
                _ms(start),
                f"найдено значений: {len(extracted)}",
            )
        if extra:
            extra = self._dedupe_extra(state, family, extracted, extra)
        state.extracted[family_id] = {key: asdict(ev) for key, ev in extracted.items()}
        state.extra[family_id] = extra
        state.duplicates[family_id] = duplicates
        values = {}
        for key, ev in extracted.items():
            spec = family.chars.get(key)
            if spec is None or ev.status == Status.ABSENT:
                continue
            values[key] = asdict(normalize_value(spec, ev))
        state.values[family_id] = values

    def _dedupe_extra(
        self, state: State, family: Family, extracted: dict, extra: list[dict]
    ) -> list[dict]:
        """Убрать из дополнительных то, что уже есть в основных, бренде или модели."""
        from apps.ste.application.llm_steps import _to_values
        from apps.ste.domain.extra_filter import (
            BRAND_NAMES,
            MODEL_NAMES,
            _value_text,
            dedupe_extra,
        )
        from apps.ste.domain.values import fold_text

        for item in extra:  # «Бренд: RAGE», а бренд модель не вынесла — вынести
            name = fold_text(str(item.get("name") or "")).strip()
            if name in BRAND_NAMES and not state.brand:
                state.brand = _value_text(item.get("value")) or None
            elif name in MODEL_NAMES and not state.model:
                state.model = _value_text(item.get("value")) or None
        filled = {
            key: (_value_text(ev.value), ev.quote)
            for key, ev in extracted.items()
            if ev.status != Status.ABSENT and ev.value not in (None, "", [])
        }
        keep, move, dropped = dedupe_extra(
            extra,
            {key: char.name for key, char in family.chars.items()},
            filled,
            state.brand,
            state.model,
        )
        for key, item in move:
            char = family.chars[key]
            values = _to_values(
                char, {**item, "status": Status.SPECIFIED.value}, state.text, Source.TEXT
            )
            if values:
                extracted[key] = values[0]
        if move or dropped:
            notes = [f"«{item.get('name')}» → «{family.chars[key].name}»" for key, item in move]
            notes += [
                f"«{item.get('name')}: {_value_text(item.get('value'))}» — {why}"
                for item, why in dropped
            ]
            self._step(
                state,
                "Дубли в дополнительных характеристиках",
                "правила (без нейросети)",
                0,
                "; ".join(notes),
            )
        return keep

    @staticmethod
    def _unify_extra(items: list[dict]) -> list[dict]:
        """Дополнительные характеристики — к словарным названиям и единицам ОКЕИ (§4.7)."""
        from apps.ste.domain.extra_filter import filter_extra
        from apps.ste.runtime import extra_unifier

        items = filter_extra(items)  # без «Описание: афигенный» — только свойства товара
        try:
            return extra_unifier().unify_all(items)
        except Exception as error:  # noqa: BLE001 — словарь необязателен
            logger.warning("Унификация дополнительных характеристик не удалась: %s", error)
            return items

    def _store_value(
        self, state: State, family: Family, ev: ExtractedValue, force_confirmed: bool = False
    ) -> None:
        spec = family.chars[ev.char_key]
        state.extracted.setdefault(family.id, {})[ev.char_key] = asdict(ev)
        nv = normalize_value(spec, ev)
        if force_confirmed:
            nv.confidence = Confidence.CONFIRMED
        state.values.setdefault(family.id, {})[ev.char_key] = asdict(nv)

    def _rule_value(self, spec: FamilyChar, text: str, source: Source) -> ExtractedValue:
        """Значение одной характеристики из короткого ответа пользователя — без LLM."""
        text = text.strip()
        if spec.is_numeric:
            number, unit = split_number_unit(text)
            return ExtractedValue(
                char_key=spec.key,
                status=Status.SPECIFIED if number is not None else Status.CONTRADICTION,
                value=number,
                unit=unit,
                quote=text,
                quote_found=True,
                source=source,
                confidence=Confidence.CONFIDENT,
            )
        value: str | list[str] = text
        if 3 in spec.kinds and ("," in text or ";" in text):
            value = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
        return ExtractedValue(
            char_key=spec.key,
            status=Status.SPECIFIED,
            value=value,
            quote=text,
            quote_found=True,
            source=source,
            confidence=Confidence.CONFIDENT,
        )

    def _value_from_option(self, spec: FamilyChar, option: str, source: Source):
        """Ответ кнопкой: вариант — текст КТРУ или описание интервала КТРУ (§4.8)."""
        nv = option_value(spec, option)
        if nv is None:
            nv = normalize_value(spec, self._rule_value(spec, option, source))
        nv.source = source
        nv.confidence = Confidence.CONFIRMED
        return nv

    # === оценка состояния → ответ ==========================================================

    def _focus_family(self, state: State) -> Family | None:
        """Семейство, по которому идёт сужение: единственный кандидат или лидер с листьями."""
        families = self._candidate_families(state)
        if not families:
            return None
        if len(families) == 1:
            return families[0][0]
        alive = [item for item in families if self._remaining(state, item[0], item[1])]
        if len(alive) == 1:
            return alive[0][0]
        return None

    def _candidate_families(self, state: State) -> list[tuple[Family, float]]:
        result = []
        for choice in state.candidates:
            family = self.kb.families.get(choice["family_id"])
            if family is not None:
                result.append((family, float(choice.get("score", 1.0))))
        return result

    def _family_values(self, state: State, family_id: str) -> dict:
        return {key: _nv_from(data) for key, data in state.values.get(family_id, {}).items()}

    def _remaining(self, state: State, family: Family, score: float) -> list:
        from apps.ste.domain.narrowing import narrow

        outcome = narrow(
            self.kb.leaves_of(family.id), self._family_values(state, family.id), {family.id: score}
        )
        return list(outcome.remaining)

    def _finish(self, state: State, session_id: str | None, total_start: float) -> dict:
        start = time.perf_counter()
        result = self.evaluate(state)
        state.timings["rules"] = _ms(start)
        self._step(
            state,
            "Нормализация, сужение до кода, вопросы, ошибки",
            "детерминированный код по КТРУ (без нейросети)",
            state.timings["rules"],
            _outcome_note(result),
        )
        state.timings["total"] = _ms(total_start)
        session_id = self.deps.store.save(session_id, state.to_json())
        result["session_id"] = session_id
        modes = {
            "fast": "поиск (однозначное совпадение, без LLM)",
            "reranker": "реранкер Qwen3-Reranker",
            "llm": f"LLM-1 ({config.LLM_MODEL}) из топ-{config.FAMILY_TOP_K} поиска",
            "fallback": "поиск (BM25 + векторы) и правила, без LLM",
            "multiple": "проверка текста: несколько товаров в одной позиции",
            "user": "выбрано пользователем",
            "empty": "—",
        }
        result["trace"] = {
            "family_by": modes.get(state.selection.get("mode", ""), state.selection.get("mode")),
            "llm1_called": "llm1" in state.timings,
            "reranker_ms": state.timings.get("rerank"),
            "llm2_called": state.timings.get("llm2", 0) > 50,
            "reranker": self.deps.reranker is not None,
            "llm_configured": self.deps.llm is not None,
            "llm_error": state.llm_note,
            "steps": list(state.steps),
        }
        result["typical_extra"] = self._typical_extra_json(
            state, (result.get("family") or {}).get("id")
        )
        text_signals = self._text_signals(state, result)
        if text_signals:
            result["signals"] = text_signals + list(result.get("signals") or [])
        result["timings_ms"] = {
            key: state.timings.get(key, 0)
            for key in ("typos", "preview", "rerank", "llm1", "llm2", "rules", "total")
        }
        return result

    def _load(self, session_id: str) -> State:
        data = self.deps.store.load(session_id) if session_id else None
        if data is None:
            raise PipelineError("Сессия не найдена — начните с описания товара.")
        state = State.from_json(data)
        state.timings = {}
        state.steps = []
        return state

    def evaluate(self, state: State) -> dict:
        """Детерминированная сборка ответа из состояния (архитектура §4.5–4.7, §6.3)."""
        from apps.ste.application.result import ResultBuilder

        return ResultBuilder(self.kb, state).build()


def config_fast_path() -> bool:
    import os

    return os.environ.get("STE_FAST_PATH", "1").strip().lower() in {"1", "true", "yes", "on"}


_COMPOUND = re.compile(
    r"(\d+)\s*(?:м|метр[а-я]*)\.?\s*(?:и\s+)?(\d{1,2})\s*(?:см|сантиметр[а-я]*)\b",
    re.IGNORECASE,
)


def join_compound_lengths(text: str) -> str:
    """«2 м 15 см», «1 метр и 30 см» → «215 см»: одно значение, а не два разных числа."""
    return _COMPOUND.sub(lambda m: f"{int(m.group(1)) * 100 + int(m.group(2))} см", text)


def _llm_note(error: Exception) -> str:
    name = type(error).__name__
    detail = f" ({name}: {str(error)[:160]})"
    if "Quota" in name:
        return "Лимит запросов к языковой модели исчерпан — подбор выполнен правилами." + detail
    return "Языковая модель недоступна — подбор выполнен правилами." + detail


__all__ = ["Deps", "Pipeline", "PipelineError", "State"]
