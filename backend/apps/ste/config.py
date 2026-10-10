"""Настройки модуля ``ste`` — из окружения, с безопасными значениями по умолчанию.

Отдельно от Django-настроек намеренно: офлайн-сборка базы знаний и замеры запускаются как
обычные скрипты, без поднятого Django, и читать конфиг им нужно одинаково.
"""

from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


#: Каталог базы знаний: ``ktru_kb.json.gz``, ``family_meta.json``, векторы семейств.
KB_DIR = Path(_env("STE_KB_DIR", str(BACKEND_DIR / "kb")))

#: Охват по префиксам ОКПД2 (архитектура §1.2). Через запятую.
SCOPE_OKPD2 = tuple(
    item.strip()
    for item in _env("STE_SCOPE_OKPD2", "14.,15.20,15.12,31.,25.73,28.24").split(",")
    if item.strip()
)

# --- LLM (архитектура §5) ------------------------------------------------------------------
#
# Один OpenAI-совместимый клиент: роутер или локальная Ollama/vLLM — только смена адреса.
# Модель по умолчанию — открытая Gemma 4 (MoE, 4B активных параметров) через роутер.

LLM_BASE_URL = _env("STE_LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
LLM_API_KEY = _env("STE_LLM_API_KEY")
LLM_MODEL = _env("STE_LLM_MODEL", "google/gemma-4-26b-a4b-it:free")
LLM_FALLBACK_MODELS = tuple(
    item.strip()
    for item in _env("STE_LLM_FALLBACK_MODELS", "google/gemma-4-31b-it:free").split(",")
    if item.strip()
)
#: ``prompt`` — схема описывается в промпте, ответ валидируется кодом (+1 повтор);
#: ``json_schema`` — ``response_format`` с JSON-схемой (ограниченное декодирование).
#: Бесплатный провайдер OpenRouter схему игнорирует (проверено), поэтому по умолчанию ``prompt``.
LLM_STRUCTURED = _env("STE_LLM_STRUCTURED", "prompt")
LLM_TIMEOUT = _float("STE_LLM_TIMEOUT", 25.0)
#: Сколько вызовов можно сделать подряд без паузы: LLM-1 и LLM-2 идут друг за другом.
LLM_BURST = int(_float("STE_LLM_BURST", 3))
#: Необязательные заголовки OpenRouter (атрибуция приложения).
LLM_REFERER = _env("STE_LLM_REFERER")
#: Только ASCII: значение уходит в HTTP-заголовок.
LLM_TITLE = _env("STE_LLM_TITLE", "Tender Hack Kazan - KTRU autofill")
#: Минимальный интервал между вызовами, секунды: бесплатный тариф — 20 запросов в минуту.
LLM_MIN_INTERVAL = _float("STE_LLM_MIN_INTERVAL", 3.1)
LLM_CACHE_DIR = Path(_env("STE_LLM_CACHE_DIR", str(BACKEND_DIR / "var" / "llm_cache")))
LLM_ENABLED = _flag("STE_LLM_ENABLED", True)

# --- эмбеддер (архитектура §3.9) -------------------------------------------------------------

EMBEDDER_PATH = _env("STE_EMBEDDER_PATH", "/opt/models/qwen3-embedding-0.6b")
EMBEDDER_ENABLED = _flag("STE_EMBEDDER_ENABLED", True)
EMBEDDER_QUERY_INSTRUCTION = (
    "Instruct: Найди тип товара в каталоге КТРУ по описанию поставщика\nQuery: "
)

#: Исправление опечаток и раскладки по словарю КТРУ перед разбором.
TYPOS_ENABLED = _flag("STE_TYPOS_ENABLED", True)

# --- пороги решения (архитектура §4.1; подбираются на тест-кейсах) ---------------------------

#: LLM-1: одно семейство, если top1 ≥ этого и отрыв от top2 ≥ FAMILY_MARGIN.
FAMILY_CONFIDENT = _float("STE_FAMILY_CONFIDENT", 0.75)
FAMILY_MARGIN = _float("STE_FAMILY_MARGIN", 0.2)
#: Кандидаты LLM-1 со score ниже — отбрасываются; если не осталось никого — «не найдено».
FAMILY_MIN = _float("STE_FAMILY_MIN", 0.4)
#: Режим LLM-1: ``top_k`` — подаётся топ-K из поиска; ``full_list`` — весь список семейств.
FAMILY_SELECT_MODE = _env("STE_FAMILY_SELECT_MODE", "top_k")
FAMILY_TOP_K = int(_float("STE_FAMILY_TOP_K", 30))
