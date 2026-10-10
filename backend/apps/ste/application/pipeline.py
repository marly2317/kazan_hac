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


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


# --- зависимости ---------------------------------------------------------------------------


@dataclass
class Deps:
    kb: KnowledgeBase
    corrector: Any  # .correct(text) -> TypoResult
    retriever: Any  # .search(text, k) -> list[FamilyHit]; .confidence(hits) -> dict
    llm: Any | None  # LlmClient | None
    store: Any  # SessionStore
    reranker: Any | None = None  # Reranker | None


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

        if not state.text.strip():
            state.selection = {"not_found": True, "mode": "empty", "reason": "Пустое описание"}
            return self._finish(state, session_id, total)

        start = time.perf_counter()
        hits = self._search(state.text, k=max(config.FAMILY_TOP_K, 10))
        state.preview = [self._hit_json(hit) for hit in hits[:5]]
        state.timings["preview"] = _ms(start)

        start = time.perf_counter()
        self._select_family(state, hits)
        state.timings["llm1"] = _ms(start)

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

                ev = extract_one(self.deps.llm, text, family, char_key)
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

    def _correct(self, text: str) -> tuple[str, list[TypoFix]]:
        try:
            result = self.deps.corrector.correct(text)
            return result.corrected, list(result.fixes)
        except Exception as error:  # noqa: BLE001 — корректор не вправе уронить подбор
            logger.warning("Корректор отказал: %s", error)
            return text, []

    def _search(self, text: str, k: int) -> list[FamilyHit]:
        try:
            return list(self.deps.retriever.search(text, k=k))
        except Exception as error:  # noqa: BLE001
            logger.warning("Поиск отказал: %s", error)
            return []

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
        order = sorted(
            range(len(top)), key=lambda i: -(1 / (61 + i) + 1 / (60 + rerank_pos[i]))
        )
        return [(top[i], 1 / (1 + math.exp(-margins[i]))) for i in order]

    def _select_family(self, state: State, hits: list[FamilyHit]) -> None:
        """Реранкер → (уверен: сразу) → LLM-1 по его топу; без LLM — по реранкеру/поиску."""
        ranked = self._rerank(state.text, hits)
        if ranked is not None:
            state.preview = [
                {**self._hit_json(hit), "score": round(prob, 4)} for hit, prob in ranked[:5]
            ]
            (top, p1) = ranked[0]
            p2 = ranked[1][1] if len(ranked) > 1 else 0.0
            if p1 >= config.RERANK_CONFIDENT and p1 - p2 >= config.RERANK_MARGIN:
                state.candidates = [asdict(FamilyChoice(top.family_id, p1, "реранкер уверен"))]
                state.selection = {"not_found": False, "mode": "reranker", "reason": "реранкер"}
                return
            if self.deps.llm is not None:
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

        if self.deps.llm is not None:
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
            if features.get("strong") or (features.get("name_overlap") or 0) >= 1.0:
                state.candidates = [
                    asdict(FamilyChoice(top.family_id, 0.8, "лучшее совпадение поиска"))
                ]
            else:
                state.candidates = [
                    asdict(FamilyChoice(hit.family_id, 0.5, "похоже по поиску")) for hit in hits[:3]
                ]
            state.selection = {"not_found": False, "mode": "fallback", "reason": "по поиску"}
        else:
            state.selection = {
                "not_found": True,
                "mode": "fallback",
                "reason": "Поиск не нашёл похожих семейств КТРУ",
            }

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
        generic = self.kb.generic()
        result = select_family(
            self.deps.llm,
            state.text,
            [short_card(family) for family in families],
            [generic_card(family) for family in generic],
            [family.short_id for family in (*families, *generic)],
        )
        choices: list[FamilyChoice] = []
        for short_id, score, reason in result.candidates:
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
            fetched = list(pool.map(lambda fid: self._call_llm2(state.text, fid), family_ids))
        for family_id, outcome in zip(family_ids, fetched, strict=True):
            self._extract(state, family_id, prefetched=outcome)

    def _call_llm2(self, text: str, family_id: str):
        from apps.ste.application.llm_steps import extract_values

        family = self.kb.families[family_id]
        if not family.chars:
            return None
        try:
            return extract_values(self.deps.llm, text, family)
        except Exception as error:  # noqa: BLE001
            return error

    def _extract(self, state: State, family_id: str, prefetched=None) -> None:
        """LLM-2 по схеме семейства (§4.3); без LLM — правила по словарю семейства."""
        family = self.kb.families[family_id]
        extracted: dict[str, ExtractedValue] = {}
        extra: list[dict] = []
        duplicates: list[dict] = []
        if self.deps.llm is not None and family.chars:
            result = (
                prefetched if prefetched is not None else self._call_llm2(state.text, family_id)
            )
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

            extracted = extract_by_rules(state.text, family)
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

    @staticmethod
    def _unify_extra(items: list[dict]) -> list[dict]:
        """Дополнительные характеристики — к словарным названиям и единицам ОКЕИ (§4.7)."""
        from apps.ste.runtime import extra_unifier

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
        state.timings["total"] = _ms(total_start)
        session_id = self.deps.store.save(session_id, state.to_json())
        result["session_id"] = session_id
        modes = {
            "fast": "поиск (однозначное совпадение, без LLM)",
            "reranker": "реранкер Qwen3-Reranker",
            "llm": "LLM-1 (Gemma) из кандидатов поиска/реранкера",
            "fallback": "правила по поиску (LLM недоступна)",
            "user": "выбрано пользователем",
            "empty": "—",
        }
        result["trace"] = {
            "family_by": modes.get(state.selection.get("mode", ""), state.selection.get("mode")),
            "llm1_called": state.timings.get("llm1", 0) > 50,
            "llm2_called": state.timings.get("llm2", 0) > 50,
            "reranker": self.deps.reranker is not None,
            "llm_configured": self.deps.llm is not None,
            "llm_error": state.llm_note,
        }
        result["timings_ms"] = {
            key: state.timings.get(key, 0)
            for key in ("typos", "preview", "llm1", "llm2", "rules", "total")
        }
        return result

    def _load(self, session_id: str) -> State:
        data = self.deps.store.load(session_id) if session_id else None
        if data is None:
            raise PipelineError("Сессия не найдена — начните с описания товара.")
        state = State.from_json(data)
        state.timings = {}
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
