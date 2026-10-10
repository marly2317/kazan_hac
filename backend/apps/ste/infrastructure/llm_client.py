"""Клиент LLM: один OpenAI-совместимый протокол для роутера и локальной Ollama/vLLM (§5).

Онлайн-пайплайн делает 1–3 вызова на запрос, а бюджет разработки — 50 бесплатных запросов в
сутки на аккаунт. Отсюда свойства, которые важнее самого вызова:

* **дисковый кэш** — ключ ``sha256(model + messages + schema + mode)``; попадание в кэш не делает
  ни одного HTTP-запроса, поэтому повтор демо и тестовых прогонов бесплатен и мгновенен;
* **структурированный ответ в двух режимах**: ``json_schema`` — ``response_format`` с JSON-схемой
  (ограниченное декодирование там, где провайдер его поддерживает); ``prompt`` — схема
  описывается в промпте, а ответ разбирает и проверяет код. Бесплатный провайдер OpenRouter
  ``response_format`` игнорирует и отвечает JSON в ```-ограде, поэтому по умолчанию ``prompt``;
* **ровно одна попытка исправления**: не прошёл ``jsonschema`` — модели показывают ошибку и
  просят прислать корректный JSON; второй провал — :class:`LlmInvalidResponse`;
* **типизированные ошибки** — пайплайн по типу решает, как деградировать: исчерпан дневной лимит
  (:class:`LlmQuotaExceeded`) — до сброса лимита больше не стучимся, отвечаем из кэша и правилами;
* **темп вызовов общий на процесс** — лимит бесплатного тарифа (20 запросов в минуту) считается
  на аккаунт, а не на экземпляр клиента.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import jsonschema

logger = logging.getLogger(__name__)

Messages = list[dict[str, str]]
#: Приводит разобранный JSON к схеме до проверки (мелкие отклонения модели — не повод тратить
#: запрос на исправление). Бросает исключение, если привести нельзя.
Prepare = Callable[[Any], Any]

STRUCTURED_MODES = ("prompt", "json_schema")
JSON_ONLY = "Ответь только JSON-объектом без пояснений."

#: Больше этого Retry-After не ждём: пользователь ждёт ответа, а у нас есть запасная модель.
RETRY_AFTER_CAP = 20.0
#: Пауза при 429 без Retry-After и перед повтором после 5xx/таймаута.
RETRY_AFTER_DEFAULT = 3.0
SERVER_RETRY_DELAY = 1.0
#: Сколько не стучаться после «дневной лимит исчерпан», если провайдер не сказал время сброса.
QUOTA_BLOCK_DEFAULT = 30 * 60.0
#: Длинные ``enum`` (сотни id семейств) не дублируем в описании схемы — они уже есть в промпте.
ENUM_RENDER_LIMIT = 12
#: Меняется, когда меняется формат файла кэша.
CACHE_VERSION = 1

_DAILY_QUOTA = re.compile(r"per[-_ ]?day|daily|в сутки|в день|суточн", re.IGNORECASE)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal", "ollama"}


# --- ошибки --------------------------------------------------------------------------------


class LlmError(Exception):
    """Любой отказ LLM. Пайплайн ловит базовый класс и переходит на правила."""


class LlmQuotaExceeded(LlmError):
    """Исчерпан дневной лимит аккаунта. Запасные модели не помогут: лимит общий на аккаунт."""


class LlmInvalidResponse(LlmError):
    """Ответ не удалось разобрать/проверить даже после попытки исправления."""

    def __init__(self, message: str, raw_text: str = "") -> None:
        super().__init__(message)
        self.raw_text = raw_text


class LlmUnavailable(LlmError):
    """Сеть, 5xx, таймауты, отказ в доступе — все модели недоступны."""


class _BadAnswer(Exception):
    """Ответ получен, но не JSON или не по схеме — повод для попытки исправления."""


class _NextModel(Exception):
    """Эта модель сейчас недоступна — пробуем следующую."""


# --- результат -----------------------------------------------------------------------------


@dataclass
class LlmResponse:
    data: dict
    #: Модель, которая фактически ответила (может быть запасной).
    model: str
    cached: bool
    latency_ms: int
    #: Число HTTP-запросов, включая повторы и исправление; 0 — ответ из кэша.
    attempts: int
    raw_text: str
    usage: dict = field(default_factory=dict)


@dataclass
class _Tally:
    attempts: int = 0
    usage: dict[str, int] = field(default_factory=dict)

    def add_usage(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int | float):
                self.usage[key] = self.usage.get(key, 0) + int(value)


# --- состояние процесса: темп и дневной лимит ---------------------------------------------


class _Throttle:
    """Не больше ``burst`` реальных вызовов за ``burst × interval`` секунд — общее на процесс.

    При ``burst = 1`` это ровно «между вызовами не меньше ``interval``». Блокировка держится и во
    время ожидания: так конкурирующие потоки выстраиваются в очередь, а не просыпаются разом.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._starts: deque[float] = deque()

    def wait(
        self,
        interval: float,
        burst: int,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        if interval <= 0:
            return
        burst = max(1, burst)
        with self._lock:
            now = clock()
            while len(self._starts) > burst:
                self._starts.popleft()
            if len(self._starts) == burst:
                delay = self._starts[0] + burst * interval - now
                if delay > 0:
                    sleep(delay)
                    # Подменённые в тестах часы во сне не идут — считаем время сами.
                    now = max(clock(), now + delay)
                self._starts.popleft()
            self._starts.append(now)

    def reset(self) -> None:
        with self._lock:
            self._starts.clear()


_THROTTLE = _Throttle()
_QUOTA_LOCK = threading.Lock()
#: Аккаунт (хэш адреса и ключа) → (до какого времени по time.time() не стучаться, сообщение).
_QUOTA_BLOCK: dict[str, tuple[float, str]] = {}


def reset_state() -> None:
    """Сбросить темп и блокировку по лимиту (для тестов и после смены ключа)."""
    _THROTTLE.reset()
    with _QUOTA_LOCK:
        _QUOTA_BLOCK.clear()


# --- разбор ответа -------------------------------------------------------------------------

_FENCE = re.compile(r"```[ \t]*(?:json|JSON|javascript)?[ \t]*\n?(.*?)```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def _first_object(chunk: str) -> dict | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", chunk):
        try:
            value, _end = decoder.raw_decode(chunk, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def extract_json(text: str | None) -> dict:
    """Первый JSON-объект из ответа модели: ограда ```json, текст до/после, висячие запятые."""
    if not text or not text.strip():
        raise ValueError("пустой ответ")
    stripped = text.strip()
    chunks = [match.group(1) for match in _FENCE.finditer(stripped)] + [stripped]
    for chunk in chunks:
        found = _first_object(chunk)
        if found is not None:
            return found
    # Частая небрежность моделей — запятая перед закрывающей скобкой. Лечим только вторым
    # проходом: внутри строк такая замена теоретически может что-то задеть.
    for chunk in chunks:
        found = _first_object(_TRAILING_COMMA.sub(r"\1", chunk))
        if found is not None:
            return found
    raise ValueError("в ответе нет JSON-объекта")


def _short(text: str, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def validation_error(data: Any, schema: dict) -> str | None:
    """Самая содержательная ошибка ``jsonschema`` с путём или ``None``, если всё верно."""
    validator = jsonschema.Draft202012Validator(schema)
    error = jsonschema.exceptions.best_match(validator.iter_errors(data))
    if error is None:
        return None
    path = "/".join(str(part) for part in error.absolute_path) or "(корень)"
    return f"{path}: {_short(error.message, 240)}"


# --- описание схемы для режима prompt ------------------------------------------------------


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_schema(schema: dict) -> str:
    """Компактная запись схемы для промпта: ``{"score": number 0..1, "id": "a" | "b"}``.

    Полная JSON-схема в промпте в разы длиннее и хуже читается моделью; длинные ``enum``
    заменяются описанием поля — сами значения уже перечислены в промпте.
    """
    return _render(schema)


def _render(node: Any) -> str:
    if not isinstance(node, dict):
        return "any"
    note = node.get("description")
    if "enum" in node:
        values = node["enum"]
        if len(values) <= ENUM_RENDER_LIMIT:
            return " | ".join(_literal(value) for value in values)
        return f"string ({note or 'одно из допустимых значений'})"
    for combinator in ("anyOf", "oneOf"):
        if combinator in node:
            text = " | ".join(_render(item) for item in node[combinator])
            return f"{text} ({note})" if note else text
    kind = node.get("type")
    if isinstance(kind, list):
        bare = {key: value for key, value in node.items() if key != "description"}
        text = " | ".join(_render({**bare, "type": item}) for item in kind)
        return f"{text} ({note})" if note else text
    if kind == "object" or "properties" in node:
        text = _render_object(node)
    elif kind == "array":
        items = node.get("items", {})
        inner = _render(items)
        if isinstance(items, dict) and (items.get("type") == "object" or "properties" in items):
            text = f"[{inner}, …]"
        else:
            text = (f"({inner})" if "|" in inner else inner) + "[]"
        if "maxItems" in node:
            text += f" (до {node['maxItems']} шт.)"
    elif kind in ("number", "integer"):
        text = kind
        if "minimum" in node or "maximum" in node:
            text += f" {node.get('minimum', '')}..{node.get('maximum', '')}"
    elif kind in ("string", "boolean", "null"):
        text = kind
    else:
        text = "any"
    if note and kind not in ("object",) and "properties" not in node:
        text += f" ({note})"
    return text


def _render_object(node: dict) -> str:
    properties: dict = node.get("properties") or {}
    required = set(node.get("required") or [])
    if not properties:
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            return "{string: " + _render(extra) + "}"
        return "{}"
    bodies = {key: json.dumps(sub, sort_keys=True) for key, sub in properties.items()}
    if len(properties) > 3 and len(set(bodies.values())) == 1:
        # Объект «id → одинаковая запись» (метаданные семейств): ключи перечисляем один раз.
        keys = " | ".join(_literal(key) for key in properties)
        return "{" + keys + ": " + _render(next(iter(properties.values()))) + "}"
    parts = []
    for key, sub in properties.items():
        mark = "" if key in required else "?"
        parts.append(f"{_literal(key)}{mark}: {_render(sub)}")
    return "{" + ", ".join(parts) + "}"


def with_schema_instruction(messages: Messages, schema: dict) -> Messages:
    """Режим prompt: дописать схему к последнему сообщению пользователя.

    Именно к последнему — изменчивой части; системный префикс остаётся одинаковым и
    кэшируется провайдером.
    """
    result = [dict(message) for message in messages]
    target = next(
        (index for index in range(len(result) - 1, -1, -1) if result[index]["role"] == "user"),
        len(result) - 1,
    )
    if target < 0:
        return result
    note = f"\n\nФормат ответа — JSON по схеме:\n{render_schema(schema)}\n{JSON_ONLY}"
    result[target]["content"] = f"{result[target]['content']}{note}"
    return result


def repair_message(error: str) -> str:
    return (
        f"Ответ не прошёл проверку: {error}. "
        "Верни исправленный ответ — один JSON-объект строго по схеме, без пояснений и без ```."
    )


# --- HTTP-разбор ---------------------------------------------------------------------------


def _payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return None


def _error_info(payload: Any, response: httpx.Response) -> tuple[str, dict]:
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        error = payload["error"]
        metadata = error.get("metadata") if isinstance(error.get("metadata"), dict) else {}
        message = str(error.get("message") or "")
        raw = metadata.get("raw")
        if raw:
            message = f"{message} {raw}".strip()
        return message or _short(response.text, 200), metadata
    return _short(response.text, 200), {}


def _rate_headers(metadata: dict) -> dict:
    headers = metadata.get("headers")
    return headers if isinstance(headers, dict) else {}


def _retry_after(response: httpx.Response, metadata: dict, now: float) -> float:
    """Пауза перед повтором при 429: Retry-After (секунды или дата) → время сброса → умолчание."""
    raw = response.headers.get("retry-after")
    delay: float | None = None
    if raw:
        try:
            delay = float(raw)
        except ValueError:
            try:
                delay = parsedate_to_datetime(raw).timestamp() - now
            except (TypeError, ValueError):
                delay = None
    if delay is None:
        reset = _rate_headers(metadata).get("X-RateLimit-Reset")
        try:
            delay = float(reset) / 1000.0 - now if reset else None
        except (TypeError, ValueError):
            delay = None
    if delay is None:
        delay = RETRY_AFTER_DEFAULT
    return min(max(delay, 0.0), RETRY_AFTER_CAP)


def _content(payload: dict) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _NextModel("в ответе нет choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # некоторые провайдеры отдают части
        content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def _is_local(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return parsed.scheme == "http" or (parsed.hostname or "") in _LOCAL_HOSTS


def _schema_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_")
    return (cleaned or "response")[:64]


# --- клиент --------------------------------------------------------------------------------


class LlmClient:
    """OpenAI-совместимый ``/chat/completions`` со структурированным ответом, кэшем и лимитами."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        fallback_models: Sequence[str] = (),
        structured_mode: str = "prompt",
        timeout: float = 60.0,
        min_interval: float = 0.0,
        cache_dir: Path | str | None = None,
        transport: httpx.BaseTransport | None = None,
        *,
        burst: int = 1,
        referer: str | None = None,
        title: str | None = None,
        disable_reasoning: bool = True,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if structured_mode not in STRUCTURED_MODES:
            raise ValueError(f"structured_mode {structured_mode!r}: ожидалось {STRUCTURED_MODES}")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.models = list(dict.fromkeys([model, *fallback_models]))
        self.structured_mode = structured_mode
        self.min_interval = min_interval
        self.burst = burst
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.disable_reasoning = disable_reasoning
        self._sleep = sleep
        self._clock = clock
        self._wall = wall_clock
        self._account = hashlib.sha256(f"{self.base_url}|{api_key}".encode()).hexdigest()[:16]
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if referer:
            headers["HTTP-Referer"] = referer
        if title:
            headers["X-Title"] = title
        # Один пул соединений на процесс: TLS-рукопожатие на каждый вызов — лишние сотни мс.
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            headers=headers,
            transport=transport,
        )
        self.stats = {"http_calls": 0, "cache_hits": 0, "repairs": 0}

    # --- публичное -----------------------------------------------------------------------

    def complete_json(
        self,
        messages: Messages,
        schema: dict,
        name: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        prepare: Prepare | None = None,
    ) -> LlmResponse:
        """Ответ модели как ``dict``, проверенный по ``schema``.

        ``prepare`` применяется к разобранному JSON до проверки — и к ответу из кэша тоже,
        поэтому в кэше лежит «сырой» объект модели, а не результат текущей версии кода.
        """
        started = time.perf_counter()
        key = self.cache_key(messages, schema)
        hit = self._cache_get(key, schema, prepare)
        if hit is not None:
            self.stats["cache_hits"] += 1
            logger.debug("LLM %s: из кэша (%s)", name, key[:12])
            return LlmResponse(
                data=hit[0],
                model=hit[1].get("model", self.model),
                cached=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
                attempts=0,
                raw_text=hit[1].get("raw_text", ""),
                usage=hit[1].get("usage", {}),
            )
        self._check_quota()

        request = (
            with_schema_instruction(messages, schema)
            if self.structured_mode == "prompt"
            else [dict(message) for message in messages]
        )
        tally = _Tally()
        text, model, index = self._exchange(
            request, schema, name, max_tokens, temperature, 0, tally
        )
        try:
            raw, data = self._parse(text, schema, prepare)
        except _BadAnswer as error:
            self.stats["repairs"] += 1
            logger.warning("LLM %s: ответ %s не по схеме (%s) — исправление", name, model, error)
            follow_up = [
                *request,
                {"role": "assistant", "content": text or ""},
                {"role": "user", "content": repair_message(str(error))},
            ]
            text, model, index = self._exchange(
                follow_up, schema, name, max_tokens, temperature, index, tally
            )
            try:
                raw, data = self._parse(text, schema, prepare)
            except _BadAnswer as second:
                raise LlmInvalidResponse(
                    f"LLM {name}: ответ не по схеме и после исправления — {second}", raw_text=text
                ) from None

        self._cache_put(
            key, {"name": name, "model": model, "data": raw, "raw_text": text, "usage": tally.usage}
        )
        return LlmResponse(
            data=data,
            model=model,
            cached=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=tally.attempts,
            raw_text=text,
            usage=dict(tally.usage),
        )

    def cache_key(self, messages: Messages, schema: dict) -> str:
        material = json.dumps(
            {
                "v": CACHE_VERSION,
                "model": self.model,
                "messages": messages,
                "schema": schema,
                "mode": self.structured_mode,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> LlmClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- кэш -----------------------------------------------------------------------------

    def _cache_path(self, key: str) -> Path | None:
        return self.cache_dir / key[:2] / f"{key}.json" if self.cache_dir else None

    def _cache_get(
        self, key: str, schema: dict, prepare: Prepare | None
    ) -> tuple[dict, dict] | None:
        path = self._cache_path(key)
        if path is None or not path.is_file():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            data = prepare(entry["data"]) if prepare else entry["data"]
        except Exception as error:  # noqa: BLE001 — битый кэш = промах, а не отказ
            logger.warning("LLM-кэш %s не читается: %s", path.name, error)
            return None
        if not isinstance(data, dict) or validation_error(data, schema) is not None:
            return None
        return data, entry

    def _cache_put(self, key: str, entry: dict) -> None:
        path = self._cache_path(key)
        if path is None:
            return
        entry = {**entry, "created_at": datetime.now(UTC).isoformat(timespec="seconds")}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Атомарно: параллельный читатель не должен увидеть половину файла.
            handle, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(entry, stream, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as error:
            logger.warning("LLM-кэш не записан: %s", error)

    # --- лимит ---------------------------------------------------------------------------

    def _check_quota(self) -> None:
        with _QUOTA_LOCK:
            blocked = _QUOTA_BLOCK.get(self._account)
            if blocked is None:
                return
            until, message = blocked
            if self._wall() >= until:
                del _QUOTA_BLOCK[self._account]
                return
        raise LlmQuotaExceeded(message)

    def _block_quota(self, message: str, metadata: dict) -> str:
        now = self._wall()
        until = now + QUOTA_BLOCK_DEFAULT
        reset = _rate_headers(metadata).get("X-RateLimit-Reset")
        try:
            if reset and float(reset) / 1000.0 > now:
                until = float(reset) / 1000.0
        except (TypeError, ValueError):
            pass
        when = datetime.fromtimestamp(until, UTC).strftime("%H:%M UTC")
        text = (
            f"Дневной лимит запросов к LLM исчерпан ({_short(message, 160)}). "
            f"Ответы из кэша доступны; новые запросы — после {when} или с другим ключом/моделью."
        )
        with _QUOTA_LOCK:
            _QUOTA_BLOCK[self._account] = (until, text)
        return text

    # --- обмен ---------------------------------------------------------------------------

    def _parse(self, text: str, schema: dict, prepare: Prepare | None) -> tuple[dict, dict]:
        try:
            raw = extract_json(text)
        except ValueError as error:
            raise _BadAnswer(f"не JSON ({error})") from None
        try:
            data = prepare(raw) if prepare else raw
        except Exception as error:  # noqa: BLE001 — любая ошибка приведения = ответ не по схеме
            raise _BadAnswer(f"не по схеме ({error})") from None
        problem = validation_error(data, schema)
        if problem is not None:
            raise _BadAnswer(problem)
        return raw, data

    def _body(
        self,
        model: str,
        messages: Messages,
        schema: dict,
        name: str,
        max_tokens: int,
        temperature: float,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.disable_reasoning:
            # Режим размышлений — секунды задержки и токены без пользы для извлечения (§5).
            body["reasoning"] = {"enabled": False}
        if self.structured_mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": _schema_name(name), "strict": True, "schema": schema},
            }
        return body

    def _exchange(
        self,
        messages: Messages,
        schema: dict,
        name: str,
        max_tokens: int,
        temperature: float,
        start: int,
        tally: _Tally,
    ) -> tuple[str, str, int]:
        """Текст ответа первой доступной модели, начиная с ``start``: (текст, модель, индекс)."""
        reasons: list[str] = []
        for index in range(start, len(self.models)):
            model = self.models[index]
            body = self._body(model, messages, schema, name, max_tokens, temperature)
            try:
                text, served = self._call_model(model, body, name, tally)
            except _NextModel as error:
                reasons.append(f"{model}: {error}")
                logger.warning("LLM %s: модель %s недоступна — %s", name, model, error)
                continue
            return text, served, index
        raise LlmUnavailable("LLM недоступна: " + "; ".join(reasons))

    def _call_model(self, model: str, body: dict, name: str, tally: _Tally) -> tuple[str, str]:
        """Один «логический» вызов модели: 429 — одна пауза и повтор, 5xx/сеть — один повтор."""
        rate_retry = server_retry = False
        url = f"{self.base_url}/chat/completions"
        while True:
            self._check_quota()
            _THROTTLE.wait(self.min_interval, self.burst, self._clock, self._sleep)
            tally.attempts += 1
            self.stats["http_calls"] += 1
            started = time.perf_counter()
            try:
                response = self._http.post(url, json=body)
            except httpx.TimeoutException as error:
                if not server_retry:
                    server_retry = True
                    self._sleep(SERVER_RETRY_DELAY)
                    continue
                raise _NextModel(f"таймаут ({type(error).__name__})") from error
            except httpx.TransportError as error:
                if not server_retry:
                    server_retry = True
                    self._sleep(SERVER_RETRY_DELAY)
                    continue
                raise _NextModel(f"сеть ({error})") from error
            latency = int((time.perf_counter() - started) * 1000)

            payload = _payload(response)
            status = response.status_code
            if status == 200 and isinstance(payload, dict) and "choices" not in payload:
                # OpenRouter иногда отдаёт ошибку провайдера телом при HTTP 200.
                error = payload.get("error")
                code = str(error.get("code", "")) if isinstance(error, dict) else ""
                status = int(code) if code.isdigit() else 502
            message, metadata = _error_info(payload, response)

            if status == 429:
                if _DAILY_QUOTA.search(message):
                    raise LlmQuotaExceeded(self._block_quota(message, metadata))
                if not rate_retry:
                    rate_retry = True
                    delay = _retry_after(response, metadata, self._wall())
                    logger.info("LLM %s: 429 у %s, пауза %.1f с", name, model, delay)
                    self._sleep(delay)
                    continue
                raise _NextModel(f"429 ({_short(message, 120)})")
            if status == 401:
                raise LlmUnavailable(f"LLM отклонила ключ (HTTP 401): {_short(message, 160)}")
            if status >= 500 or status == 408:
                if not server_retry:
                    server_retry = True
                    self._sleep(SERVER_RETRY_DELAY)
                    continue
                raise _NextModel(f"HTTP {status} ({_short(message, 120)})")
            if status != 200 or not isinstance(payload, dict):
                raise _NextModel(f"HTTP {status} ({_short(message, 120)})")

            text = _content(payload)
            tally.add_usage(payload.get("usage"))
            usage = payload.get("usage") or {}
            served = str(payload.get("model") or model)
            logger.info(
                "LLM %s: %s %d мс, токены %s→%s",
                name,
                served,
                latency,
                usage.get("prompt_tokens", "?"),
                usage.get("completion_tokens", "?"),
            )
            return text, served


def get_llm_client(**overrides: Any) -> LlmClient | None:
    """Клиент из :mod:`apps.ste.config`; ``None``, если LLM выключена или нет ключа.

    Без ключа допускается только свой сервер (http:// или localhost): локальной Ollama/vLLM
    ключ не нужен, а удалённому роутеру без ключа стучаться бессмысленно.
    """
    from apps.ste import config

    if not config.LLM_ENABLED:
        return None
    if not config.LLM_API_KEY and not _is_local(config.LLM_BASE_URL):
        logger.info("LLM выключена: нет STE_LLM_API_KEY для %s", config.LLM_BASE_URL)
        return None
    params: dict[str, Any] = {
        "base_url": config.LLM_BASE_URL,
        "api_key": config.LLM_API_KEY,
        "model": config.LLM_MODEL,
        "fallback_models": config.LLM_FALLBACK_MODELS,
        "structured_mode": config.LLM_STRUCTURED,
        "timeout": config.LLM_TIMEOUT,
        "min_interval": config.LLM_MIN_INTERVAL,
        "cache_dir": config.LLM_CACHE_DIR,
        "burst": int(getattr(config, "LLM_BURST", 1)),
        "referer": getattr(config, "LLM_REFERER", None) or None,
        "title": getattr(config, "LLM_TITLE", None) or None,
    }
    params.update(overrides)
    return LlmClient(**params)
