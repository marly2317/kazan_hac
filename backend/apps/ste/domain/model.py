"""Общие типы онлайн-пайплайна: то, чем обмениваются шаги (архитектура §4, §6, §8).

Модуль — контракт между шагами. Шаги:

1. опечатки → исправленный текст + список исправлений (:class:`TypoFix`);
2. предпросмотр (поиск) → :class:`FamilyHit`;
3. LLM-1 → :class:`FamilyChoice`;
4. LLM-2 → :class:`ExtractedValue` по каждой характеристике семейства;
5. код: нормализация → :class:`NormalizedValue`;
6. код: сужение листьев, вопрос, дозаполнение;
7. код: карточка СТЕ + сигналы ошибок (:class:`Signal`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class Status(StrEnum):
    """Статус характеристики в ответе LLM-2 (архитектура §4.3)."""

    SPECIFIED = "указано"
    ABSENT = "не указано"
    CONTRADICTION = "противоречие"


class Confidence(StrEnum):
    """Сила значения (архитектура §4.4, §4.8). Порядок: confirmed > confident > weak."""

    CONFIRMED = "confirmed"  # ответ кнопкой или ручная правка — перезаписывать нельзя
    CONFIDENT = (
        "confident"  # «указано» + цитата найдена + (для чисел) единица и размерность в порядке
    )
    WEAK = "weak"  # всё остальное: не исключает листья, только штрафует


class Source(StrEnum):
    TEXT = "text"
    ANSWER = "answer"
    EDIT = "edit"
    KTRU = "ktru"  # дозаполнено по КТРУ: у листа единственное допустимое значение


class Stage(StrEnum):
    """Стадия ответа (архитектура §6.3)."""

    PREVIEW = "preview"
    FAMILY_SELECTED = "family_selected"
    NARROWING = "narrowing"
    QUESTION = "question"
    DONE = "done"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"


class SignalType(StrEnum):
    """Сигналы для модуля ошибок (архитектура §8.2) + то, что добавляет модуль ошибок."""

    TYPO = "TYPO"  # исправлена опечатка / раскладка
    VALUE_NOT_ALLOWED = "VALUE_NOT_ALLOWED"  # значение вне списка допустимых
    OUT_OF_RANGE = "OUT_OF_RANGE"  # число не попало ни в один интервал
    UNIT_MISMATCH = "UNIT_MISMATCH"  # размерность единицы не совпала
    UNKNOWN_UNIT = "UNKNOWN_UNIT"  # единица не распознана
    CONTRADICTION = "CONTRADICTION"  # текст противоречит сам себе / допустимым значениям
    DUPLICATE_VALUE = "DUPLICATE_VALUE"  # одна характеристика — разные значения
    CONFLICT = "CONFLICT"  # значения вместе исключили все листья
    MISSING_REQUIRED = "MISSING_REQUIRED"  # обязательная не указана (после определения кода)
    NOT_FOUND = "NOT_FOUND"  # тип товара не определён
    QUOTE_NOT_FOUND = "QUOTE_NOT_FOUND"  # служебный: цитата модели не найдена в тексте
    NOTICE = "NOTICE"  # служебное уведомление: LLM недоступна, подбор выполнен правилами


class Severity(StrEnum):
    ERROR = "error"  # значение неверно и мешает подбору
    WARNING = "warning"  # подозрительно / нужно действие пользователя
    INFO = "info"  # исправлено автоматически, к сведению


@dataclass
class TypoFix:
    """Одна правка корректора: что было, что стало, почему."""

    original: str
    corrected: str
    #: layout | dictionary | llm
    reason: str
    start: int = -1
    end: int = -1


@dataclass
class FamilyHit:
    """Кандидат-семейство из предпросмотра (поиск по индексу, архитектура §4.0)."""

    family_id: str
    short_id: str
    name: str
    score: float
    bm25_rank: int | None = None
    vector_rank: int | None = None
    cosine: float | None = None


@dataclass
class FamilyChoice:
    """Кандидат-семейство от LLM-1 (архитектура §4.1)."""

    family_id: str
    score: float
    reason: str = ""
    fixed_by_user: bool = False


@dataclass
class ExtractedValue:
    """Значение характеристики после LLM-2 и проверки цитаты (архитектура §4.3–4.4)."""

    char_key: str
    status: Status
    #: Качественная: строка из enum; kind=3: список строк; числовая: число.
    value: str | float | list[str] | None = None
    #: Единица, как написана в тексте (только для числовых).
    unit: str | None = None
    quote: str | None = None
    quote_found: bool = False
    source: Source = Source.TEXT
    confidence: Confidence = Confidence.WEAK


@dataclass
class NormalizedValue:
    """Значение, приведённое к КТРУ: единица распознана, переведена, сопоставлена."""

    char_key: str
    char_name: str
    #: Как написал пользователь: «125мм», «болгарка».
    user_text: str
    #: text | number | multi
    value_type: str
    #: Для качественных — значения КТРУ (enum), для чисел — пусто.
    texts: list[str] = field(default_factory=list)
    number: float | None = None
    #: Единица пользователя (распознанная) и единица характеристики КТРУ.
    user_unit: str | None = None
    ktru_unit: str | None = None
    #: Число в единице КТРУ.
    number_in_ktru_unit: float | None = None
    #: Описания допустимых значений КТРУ, в которые попало значение: «≥125 <130 Миллиметр».
    matched: list[str] = field(default_factory=list)
    #: Единица распознана и размерность совпала (для качественных всегда True).
    unit_ok: bool = True
    confidence: Confidence = Confidence.WEAK
    source: Source = Source.TEXT
    quote: str | None = None

    @property
    def was_unified(self) -> bool:
        """Значение пришлось переводить/приводить — интерфейс показывает переход."""
        if self.value_type == "number":
            return bool(self.user_unit and self.ktru_unit and self.user_unit != self.ktru_unit)
        return bool(self.texts) and self.user_text.casefold() not in {
            text.casefold() for text in self.texts
        }


@dataclass
class Signal:
    """Факт об ошибке/несоответствии + человеческое объяснение (архитектура §8.2)."""

    type: SignalType
    severity: Severity
    message: str
    char: str | None = None
    char_key: str | None = None
    user_value: str | None = None
    user_unit: str | None = None
    quote: str | None = None
    allowed: list[str] = field(default_factory=list)
    nearest_allowed: str | None = None
    #: Конкретный шаг: «Замените на …», «Укажите …».
    suggestion: str | None = None
    family_id: str | None = None
    leaf_code: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class QuestionOption:
    label: str
    #: Значение, которое уйдёт в ``answer``: текст КТРУ или описание интервала.
    value: str
    #: Сколько листьев останется, если выбрать этот вариант.
    remaining: int


@dataclass
class Question:
    """Уточняющий вопрос (архитектура §4.6)."""

    char_key: str | None  # None — вопрос о выборе семейства
    title: str
    why: str
    options: list[QuestionOption]
    allow_free_text: bool = True
    #: char | family | leaf
    kind: str = "char"


@dataclass
class FunnelStep:
    level: str
    label: str
    count: int
