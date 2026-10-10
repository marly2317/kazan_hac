"""LLM-метаданные семейств (офлайн, архитектура §3.7): синонимы, описание, ключевые слова.

Синонимы — мост между языком поставщика («болгарка», «УШМ») и наименованиями КТРУ
(«Шлифмашина»): ими питаются карточки семейств, поиск и промпт LLM-1. Генерация пакетами
(по умолчанию 25 семейств → ~12 запросов на весь охват) и с продолжением: готовые семейства
пропускаются, файл переписывается после каждого пакета — обрыв по дневному лимиту не теряет
сделанного. Результат — ``kb/family_meta.json``, ключ — код корня семейства; его стоит
просмотреть глазами (§3.7) — правки руками переживают следующий запуск.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apps.ste.application import prompts as P
from apps.ste.application.llm_steps import JsonLlm
from apps.ste.domain.kb import Family
from apps.ste.domain.values import fold_text

logger = logging.getLogger(__name__)

META_FILE = "family_meta.json"
DEFAULT_BATCH = 25
MAX_SYNONYMS = 15
MAX_KEYWORDS = 8
MAX_DESCRIPTION = 300
#: ~150 токенов ответа на семейство + запас: обрезанный JSON — это потерянный пакет.
TOKENS_PER_FAMILY = 220

#: Бренды категорий хакатона: синонимом типа товара они быть не могут. Дополняется словарём
#: брендов из выгрузок СТЕ (``--brands``).
DEFAULT_BRANDS = frozenset(
    {
        # инструменты
        "makita", "макита", "bosch", "бош", "dewalt", "девольт", "деволт", "metabo", "метабо",
        "hitachi", "хитачи", "hikoki", "ryobi", "риоби", "stanley", "стэнли", "milwaukee",
        "милуоки", "hilti", "хилти", "einhell", "aeg", "festool", "kress", "black+decker",
        "интерскол", "interskol", "зубр", "zubr", "ставр", "stavr", "калибр", "вихрь", "энкор",
        "диолд", "фиолент", "ресанта", "resanta", "patriot", "elitech", "sturm", "kolner",
        "kraton", "bort", "matrix", "stayer", "gross", "sparta", "fit", "jet", "karcher",
        "керхер", "dremel", "дремель", "skil", "worx", "greenworks", "hammer", "deko",
        # одежда, обувь, сумки
        "nike", "найк", "adidas", "адидас", "puma", "пума", "reebok", "рибок", "columbia",
        "коламбия", "nordman", "техноавиа", "восток-сервис", "zara", "gloria", "jeans", "o'stin",
        "ostin", "baon", "finn", "flare", "walker", "samsonite", "ecco", "экко", "salomon",
        "the north face", "lacoste", "levi's", "levis",
        # мебель
        "ikea", "икеа", "бюрократ", "chairman", "шатура", "лазурит", "hoff", "хофф",
    }
)  # fmt: skip

#: Похоже на модель/артикул: буквы вперемешку с цифрами («ga5030», «psr 14.4»).
_MODEL_LIKE = re.compile(r"[a-z]+[-\s]?\d|\d+[a-z]{1,3}\b", re.IGNORECASE)
_WORDS = re.compile(r"[0-9a-zа-яё+'-]+", re.IGNORECASE)


@dataclass
class MetaRun:
    """Итог запуска генерации."""

    meta: dict[str, dict]
    done: list[str] = field(default_factory=list)
    #: Семейства, которые модель пропустила или отдала пустыми — их возьмёт следующий запуск.
    missing: list[str] = field(default_factory=list)
    requests: int = 0
    cached: int = 0
    stopped: str | None = None


# --- файл ----------------------------------------------------------------------------------


def load_meta(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: ожидался объект «код семейства → метаданные»")
    return data


def save_meta(path: Path, meta: dict[str, dict]) -> None:
    """Атомарно и с сортировкой по коду: стабильный дифф для ручной проверки в git."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(dict(sorted(meta.items())), stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    os.replace(tmp, path)


def load_brands(path: Path | None) -> frozenset[str]:
    """Бренды: встроенный список + файл (JSON-список или по одному в строке)."""
    brands = set(DEFAULT_BRANDS)
    if path is not None:
        raw = path.read_text(encoding="utf-8")
        try:
            items: Iterable[Any] = json.loads(raw)
        except json.JSONDecodeError:
            items = raw.splitlines()
        brands.update(fold_text(str(item)) for item in items if str(item).strip())
    return frozenset(brands)


# --- очистка ответа ------------------------------------------------------------------------


def _is_brand(phrase: str, brands: frozenset[str]) -> bool:
    folded = fold_text(phrase)
    if folded in brands:
        return True
    return any(fold_text(word) in brands for word in _WORDS.findall(folded))


def _clean_list(raw: Any, limit: int, brands: frozenset[str], exclude: set[str]) -> list[str]:
    if isinstance(raw, str):
        raw = re.split(r"[;,\n]", raw)
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen = set(exclude)
    for item in raw:
        if not isinstance(item, str):
            continue
        phrase = re.sub(r"\s+", " ", item).strip(" \t.,;:«»\"'").lower()
        if not phrase or len(phrase) > 60:
            continue
        if _is_brand(phrase, brands) or _MODEL_LIKE.search(phrase):
            continue
        key = phrase.replace("ё", "е")
        if key in seen:
            continue
        seen.add(key)
        result.append(phrase)
        if len(result) >= limit:
            break
    return result


def clean_entry(raw: Any, family: Family, brands: frozenset[str] = DEFAULT_BRANDS) -> dict | None:
    """Ответ модели по одному семейству → запись ``family_meta.json``; ``None`` — пусто.

    Синонимы: нижний регистр, без дублей, без брендов и артикулов, без самого названия.
    """
    if not isinstance(raw, dict):
        return None
    own_name = {family.name.lower().replace("ё", "е")}
    synonyms = _clean_list(raw.get("synonyms"), MAX_SYNONYMS, brands, own_name)
    keywords = _clean_list(raw.get("keywords"), MAX_KEYWORDS, brands, set())
    description = re.sub(r"\s+", " ", str(raw.get("description") or "")).strip()
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 1].rstrip() + "…"
    if not synonyms and not description:
        return None
    return {
        "short_id": family.short_id,
        "name": family.name,
        "synonyms": synonyms,
        "description": description,
        "keywords": keywords,
    }


# --- генерация -----------------------------------------------------------------------------


def meta_schema(short_ids: list[str]) -> dict:
    """Объект «id → запись». Поля не обязательны: пропущенное семейство возьмёт следующий
    запуск, а повторный запрос ради одного пропуска дороже."""
    entry = {
        "type": "object",
        "properties": {
            "synonyms": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["synonyms", "description", "keywords"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {short_id: entry for short_id in short_ids},
        "additionalProperties": False,
    }


def _prepare_meta(short_ids: list[str]) -> Callable[[Any], dict]:
    by_number = {
        int(match.group(1)): short_id
        for short_id in short_ids
        if (match := re.fullmatch(r"[A-Za-z]+0*(\d+)", short_id))
    }

    def prepare(obj: Any) -> dict:
        if not isinstance(obj, dict):
            raise ValueError("ожидался JSON-объект")
        if len(obj) == 1 and isinstance(next(iter(obj.values())), dict):
            inner = next(iter(obj.values()))
            if all(isinstance(value, dict) for value in inner.values()) and inner:
                obj = inner  # {"families": {"F001": {...}}}
        result: dict[str, dict] = {}
        for key, value in obj.items():
            match = re.search(r"(\d+)", str(key))
            short_id = (
                key if key in short_ids else (by_number.get(int(match.group(1))) if match else None)
            )
            if short_id is None or not isinstance(value, dict):
                continue
            synonyms = value.get("synonyms")
            keywords = value.get("keywords")
            result[short_id] = {
                "synonyms": [str(item) for item in synonyms] if isinstance(synonyms, list) else [],
                "description": str(value.get("description") or ""),
                "keywords": [str(item) for item in keywords] if isinstance(keywords, list) else [],
            }
        return result

    return prepare


def is_done(entry: Any) -> bool:
    return isinstance(entry, dict) and bool(entry.get("synonyms"))


def pending(families: list[Family], existing: dict[str, dict], force: bool = False) -> list[Family]:
    return [family for family in families if force or not is_done(existing.get(family.id))]


def batches(families: list[Family], size: int = DEFAULT_BATCH) -> list[list[Family]]:
    size = max(1, size)
    return [families[start : start + size] for start in range(0, len(families), size)]


def prompt_size(families: list[Family]) -> int:
    """Символов в промпте пакета — для ``--dry-run`` (кириллица ≈ 3 символа на токен)."""
    return sum(len(message["content"]) for message in P.family_meta_messages(families))


def generate_batch(
    client: JsonLlm, families: list[Family], brands: frozenset[str] = DEFAULT_BRANDS
) -> tuple[dict[str, dict], Any]:
    """Один запрос: метаданные пакета семейств, ключ — код корня семейства."""
    short_ids = [family.short_id for family in families]
    response = client.complete_json(
        P.family_meta_messages(families),
        meta_schema(short_ids),
        "family_meta",
        max_tokens=TOKENS_PER_FAMILY * len(families) + 200,
        temperature=0.0,
        prepare=_prepare_meta(short_ids),
    )
    result: dict[str, dict] = {}
    for family in families:
        entry = clean_entry(response.data.get(family.short_id), family, brands)
        if entry is not None:
            result[family.id] = entry
    return result, response


def generate_meta(
    client: JsonLlm,
    families: list[Family],
    existing: dict[str, dict],
    *,
    batch_size: int = DEFAULT_BATCH,
    max_batches: int | None = None,
    brands: frozenset[str] = DEFAULT_BRANDS,
    force: bool = False,
    on_batch: Callable[[dict[str, dict]], None] | None = None,
) -> MetaRun:
    """Сгенерировать недостающие метаданные. ``on_batch`` получает всё накопленное после
    каждого пакета — чтобы сохранить прогресс до следующего запроса."""
    from apps.ste.infrastructure.llm_client import (
        LlmInvalidResponse,
        LlmQuotaExceeded,
        LlmUnavailable,
    )

    meta = dict(existing)
    run = MetaRun(meta=meta)
    todo = batches(pending(families, existing, force), batch_size)
    if max_batches is not None:
        todo = todo[: max(0, max_batches)]
    for index, batch in enumerate(todo, start=1):
        try:
            entries, response = generate_batch(client, batch, brands)
        except LlmQuotaExceeded as error:
            run.stopped = str(error)
            break
        except LlmUnavailable as error:
            run.stopped = str(error)
            break
        except LlmInvalidResponse as error:
            logger.warning("Пакет %d (%s…) пропущен: %s", index, batch[0].short_id, error)
            run.missing.extend(family.id for family in batch)
            continue
        run.requests += getattr(response, "attempts", 0)
        run.cached += int(bool(getattr(response, "cached", False)))
        meta.update(entries)
        run.done.extend(entries)
        run.missing.extend(family.id for family in batch if family.id not in entries)
        logger.info("Пакет %d/%d: %d из %d семейств", index, len(todo), len(entries), len(batch))
        if on_batch is not None:
            on_batch(meta)
    return run
