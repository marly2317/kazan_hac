"""LLM-клиент: только httpx.MockTransport — ни одного запроса к настоящему API."""

from __future__ import annotations

import json
import logging
import time

import httpx
import pytest

from apps.ste.infrastructure import llm_client as L
from apps.ste.infrastructure.llm_client import (
    LlmClient,
    LlmInvalidResponse,
    LlmQuotaExceeded,
    LlmUnavailable,
    extract_json,
    render_schema,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["да", "нет"]},
        "score": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["answer", "score"],
    "additionalProperties": False,
}
MESSAGES = [
    {"role": "system", "content": "Ты отвечаешь да или нет."},
    {"role": "user", "content": "Болгарка — это шлифмашина?"},
]
GOOD = '{"answer": "да", "score": 0.9}'

DAILY_LIMIT = {
    "error": {
        "message": "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free "
        "model requests per day",
        "code": 429,
        "metadata": {
            "headers": {
                "X-RateLimit-Limit": "50",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int((time.time() + 3600) * 1000)),
            },
            "provider_name": None,
        },
    },
    "user_id": "user_x",
}
PER_MINUTE = {"error": {"message": "Rate limit exceeded: free-models-per-min.", "code": 429}}


def completion(content, model: str = "m1", usage: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "gen-1",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
    )


class Fake:
    """Очередь ответов транспорта + журнал запросов."""

    def __init__(self, *items) -> None:
        self.queue = list(items)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.queue:
            raise AssertionError("неожиданный HTTP-запрос")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(request.content) for request in self.requests]

    @property
    def models(self) -> list[str]:
        return [body["model"] for body in self.bodies]


def no_http(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"HTTP-запрос не ожидался: {request.url}")


@pytest.fixture(autouse=True)
def _clean_state():
    L.reset_state()
    yield
    L.reset_state()


@pytest.fixture
def sleeps() -> list[float]:
    return []


def make(fake, sleeps=None, **overrides) -> LlmClient:
    params = {
        "base_url": "https://router.test/api/v1/",
        "api_key": "sk-test",
        "model": "m1",
        "fallback_models": ["m2"],
        "structured_mode": "prompt",
        "timeout": 5.0,
        "min_interval": 0.0,
        "cache_dir": None,
        "transport": httpx.MockTransport(fake),
        "sleep": (sleeps.append if sleeps is not None else (lambda _seconds: None)),
    }
    params.update(overrides)
    return LlmClient(**params)


# --- запрос ----------------------------------------------------------------------------------


def test_prompt_mode_request_shape():
    fake = Fake(completion(GOOD))
    client = make(fake, referer="https://hack.test", title="KTRU")
    response = client.complete_json(MESSAGES, SCHEMA, "yes_no", max_tokens=123)

    assert response.data == {"answer": "да", "score": 0.9}
    assert response.cached is False and response.attempts == 1 and response.model == "m1"
    assert response.usage == {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    request = fake.requests[0]
    assert str(request.url) == "https://router.test/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    assert request.headers["http-referer"] == "https://hack.test"
    assert request.headers["x-title"] == "KTRU"
    body = fake.bodies[0]
    assert body["model"] == "m1" and body["temperature"] == 0 and body["max_tokens"] == 123
    assert body["reasoning"] == {"enabled": False}
    assert "response_format" not in body
    # Системный префикс не тронут (кэш провайдера), схема — в последнем сообщении пользователя.
    assert body["messages"][0] == MESSAGES[0]
    user = body["messages"][-1]["content"]
    assert user.startswith(MESSAGES[1]["content"])
    assert '"answer": "да" | "нет"' in user and '"score": number 0..1' in user
    assert user.rstrip().endswith(L.JSON_ONLY)
    # Исходные сообщения вызывающего не изменены.
    assert MESSAGES[1]["content"] == "Болгарка — это шлифмашина?"


def test_json_schema_mode_sends_response_format():
    fake = Fake(completion(GOOD))
    client = make(fake, structured_mode="json_schema")
    client.complete_json(MESSAGES, SCHEMA, "select family!")
    body = fake.bodies[0]
    assert body["messages"] == MESSAGES
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "select_family", "strict": True, "schema": SCHEMA},
    }


def test_unknown_structured_mode_rejected():
    with pytest.raises(ValueError):
        make(Fake(), structured_mode="tools")


def test_no_key_no_authorization_header():
    fake = Fake(completion(GOOD))
    make(fake, api_key="").complete_json(MESSAGES, SCHEMA, "x")
    assert "authorization" not in fake.requests[0].headers


# --- разбор и исправление ---------------------------------------------------------------------


def test_fenced_answer_with_prose_is_parsed():
    content = 'Вот ответ:\n```json\n{"answer": "нет", "score": 0.2}\n```\nГотово.'
    response = make(Fake(completion(content))).complete_json(MESSAGES, SCHEMA, "x")
    assert response.data == {"answer": "нет", "score": 0.2}
    assert response.raw_text == content


def test_content_as_list_of_parts():
    parts = [{"type": "text", "text": '{"answer": "да",'}, {"type": "text", "text": '"score": 1}'}]
    response = make(Fake(completion(parts))).complete_json(MESSAGES, SCHEMA, "x")
    assert response.data == {"answer": "да", "score": 1}


def test_fenced_nonconforming_answer_goes_through_one_repair(sleeps):
    bad = '```json\n{"result": "yes", "confidence": "high"}\n```'
    fake = Fake(completion(bad), completion(GOOD, usage={"prompt_tokens": 7, "total_tokens": 9}))
    client = make(fake, sleeps)
    response = client.complete_json(MESSAGES, SCHEMA, "x")

    assert response.data == {"answer": "да", "score": 0.9}
    assert response.attempts == 2 and client.stats["repairs"] == 1
    assert response.usage["prompt_tokens"] == 107 and response.usage["total_tokens"] == 129
    repair = fake.bodies[1]["messages"]
    assert repair[-2] == {"role": "assistant", "content": bad}
    assert repair[-1]["role"] == "user" and "не прошёл проверку" in repair[-1]["content"]
    assert fake.models == ["m1", "m1"]  # исправление — у той же модели


def test_second_invalid_answer_raises_and_is_not_cached(tmp_path):
    fake = Fake(completion("не знаю"), completion('{"answer": "может быть", "score": 2}'))
    client = make(fake, cache_dir=tmp_path)
    with pytest.raises(LlmInvalidResponse) as error:
        client.complete_json(MESSAGES, SCHEMA, "x")
    assert len(fake.requests) == 2
    assert error.value.raw_text == '{"answer": "может быть", "score": 2}'
    assert not list(tmp_path.rglob("*.json"))


def test_empty_content_triggers_repair():
    fake = Fake(completion(None), completion(GOOD))
    response = make(fake).complete_json(MESSAGES, SCHEMA, "x")
    assert response.data["answer"] == "да" and response.attempts == 2


def test_prepare_fixes_minor_deviation_without_extra_request():
    def prepare(obj):
        return {
            "answer": obj["answer"].lower(),
            "score": float(str(obj["score"]).replace(",", ".")),
        }

    fake = Fake(completion('{"answer": "ДА", "score": "0,8"}'))
    response = make(fake).complete_json(MESSAGES, SCHEMA, "x", prepare=prepare)
    assert response.data == {"answer": "да", "score": 0.8}
    assert len(fake.requests) == 1


def test_prepare_exception_counts_as_bad_answer():
    def prepare(obj):
        raise ValueError("сломано")

    fake = Fake(completion(GOOD), completion(GOOD))
    with pytest.raises(LlmInvalidResponse):
        make(fake).complete_json(MESSAGES, SCHEMA, "x", prepare=prepare)
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": {"b": [1, 2]}}\n```', {"a": {"b": [1, 2]}}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('Ответ: {"a": "скобка } внутри", "b": "кавычка \\" тоже"} — всё', None),
        ('{не json} а вот {"a": 2}', {"a": 2}),
        ('```json\n{"a": [1, 2,], "b": 3,}\n```', {"a": [1, 2], "b": 3}),
        ('```json\n{"a": 1}', {"a": 1}),  # незакрытая ограда
        ('[1, 2] {"a": 3}', {"a": 3}),  # список не подходит — ищем объект
    ],
)
def test_extract_json(text, expected):
    if expected is None:
        expected = {"a": "скобка } внутри", "b": 'кавычка " тоже'}
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["", "   ", None, "нет json", "[1, 2]", "{broken"])
def test_extract_json_rejects(text):
    with pytest.raises(ValueError):
        extract_json(text)


def test_render_schema_compacts_long_enums_and_repeated_entries():
    ids = [f"F{index:03d}" for index in range(1, 300)]
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {"id": {"enum": ids, "description": "id из списка"}},
                    "required": ["id"],
                },
            },
            "note": {"type": ["string", "null"], "description": "кратко"},
            "opt": {"type": "boolean"},
        },
        "required": ["items", "note"],
    }
    text = render_schema(schema)
    assert "F150" not in text and "string (id из списка)" in text
    assert '"items": [{"id": string (id из списка)}, …] (до 3 шт.)' in text
    assert '"note": string | null (кратко)' in text and '"opt"?: boolean' in text
    entry = {"type": "object", "properties": {"s": {"type": "array", "items": {"type": "string"}}}}
    repeated = {"type": "object", "properties": {f"F00{i}": entry for i in range(1, 6)}}
    assert (
        render_schema(repeated) == '{"F001" | "F002" | "F003" | "F004" | "F005": {"s"?: string[]}}'
    )


# --- лимиты и запасные модели ----------------------------------------------------------------


def test_daily_quota_raises_without_fallback_and_blocks_further_calls(tmp_path):
    fake = Fake(completion(GOOD), httpx.Response(429, json=DAILY_LIMIT))
    client = make(fake, cache_dir=tmp_path)
    client.complete_json(MESSAGES, SCHEMA, "x")  # закэшировали
    other = [*MESSAGES[:1], {"role": "user", "content": "Дрель — это шлифмашина?"}]

    with pytest.raises(LlmQuotaExceeded) as error:
        client.complete_json(other, SCHEMA, "x")
    assert "Дневной лимит" in str(error.value)
    assert fake.models == ["m1", "m1"]  # запасная модель не пробовалась: лимит на аккаунт

    # Новый клиент с тем же ключом не стучится вовсе, но кэш по-прежнему отвечает.
    blocked = make(no_http, cache_dir=tmp_path)
    with pytest.raises(LlmQuotaExceeded):
        blocked.complete_json(other, SCHEMA, "x")
    assert blocked.complete_json(MESSAGES, SCHEMA, "x").cached is True
    # Другой ключ — другой аккаунт, блокировка его не касается.
    assert make(Fake(completion(GOOD)), api_key="sk-other").complete_json(other, SCHEMA, "x")


def test_quota_block_expires(tmp_path):
    now = [1_000_000.0]
    limit = {"error": {"message": "free-models-per-day exceeded", "code": 429}}
    fake = Fake(httpx.Response(429, json=limit), completion(GOOD))
    client = make(fake, wall_clock=lambda: now[0])
    with pytest.raises(LlmQuotaExceeded):
        client.complete_json(MESSAGES, SCHEMA, "x")
    now[0] += L.QUOTA_BLOCK_DEFAULT + 1
    assert client.complete_json(MESSAGES, SCHEMA, "x").data["answer"] == "да"


def test_429_honours_retry_after_then_succeeds(sleeps):
    fake = Fake(
        httpx.Response(429, json=PER_MINUTE, headers={"Retry-After": "2"}), completion(GOOD)
    )
    response = make(fake, sleeps).complete_json(MESSAGES, SCHEMA, "x")
    assert response.data["answer"] == "да" and response.attempts == 2
    assert sleeps == [2.0] and fake.models == ["m1", "m1"]


def test_retry_after_is_capped(sleeps):
    fake = Fake(
        httpx.Response(429, json=PER_MINUTE, headers={"Retry-After": "600"}), completion(GOOD)
    )
    make(fake, sleeps).complete_json(MESSAGES, SCHEMA, "x")
    assert sleeps == [L.RETRY_AFTER_CAP]


def test_429_twice_moves_to_fallback_model(sleeps):
    fake = Fake(
        httpx.Response(429, json=PER_MINUTE),
        httpx.Response(429, json=PER_MINUTE),
        completion(GOOD, model="m2"),
    )
    response = make(fake, sleeps).complete_json(MESSAGES, SCHEMA, "x")
    assert fake.models == ["m1", "m1", "m2"]
    assert response.model == "m2" and response.attempts == 3
    assert sleeps == [L.RETRY_AFTER_DEFAULT]


def test_5xx_retry_once_then_fallback(sleeps):
    fake = Fake(
        httpx.Response(502, json={"error": {"message": "bad gateway"}}),
        httpx.Response(503, text="upstream down"),
        completion(GOOD, model="m2"),
    )
    response = make(fake, sleeps).complete_json(MESSAGES, SCHEMA, "x")
    assert fake.models == ["m1", "m1", "m2"] and response.model == "m2"
    assert sleeps == [L.SERVER_RETRY_DELAY]


def test_5xx_retry_on_same_model_can_succeed():
    fake = Fake(httpx.Response(500), completion(GOOD))
    assert make(fake).complete_json(MESSAGES, SCHEMA, "x").attempts == 2
    assert fake.models == ["m1", "m1"]


def test_error_body_with_http_200_is_retried():
    error = httpx.Response(200, json={"error": {"code": 502, "message": "provider returned error"}})
    fake = Fake(error, completion(GOOD))
    assert make(fake).complete_json(MESSAGES, SCHEMA, "x").data["answer"] == "да"
    assert fake.models == ["m1", "m1"]


def test_timeouts_on_all_models_raise_unavailable():
    timeouts = [httpx.ReadTimeout("slow") for _ in range(4)]
    fake = Fake(*timeouts)
    with pytest.raises(LlmUnavailable) as error:
        make(fake).complete_json(MESSAGES, SCHEMA, "x")
    assert fake.models == ["m1", "m1", "m2", "m2"]
    assert "m1" in str(error.value) and "m2" in str(error.value)


def test_connect_error_then_fallback():
    fake = Fake(httpx.ConnectError("refused"), httpx.ConnectError("refused"), completion(GOOD))
    assert make(fake).complete_json(MESSAGES, SCHEMA, "x").attempts == 3


def test_401_is_fatal_no_fallback():
    fake = Fake(httpx.Response(401, json={"error": {"message": "No auth credentials found"}}))
    with pytest.raises(LlmUnavailable):
        make(fake).complete_json(MESSAGES, SCHEMA, "x")
    assert len(fake.requests) == 1


def test_404_unknown_model_moves_to_fallback():
    fake = Fake(
        httpx.Response(404, json={"error": {"message": "No endpoints found"}}), completion(GOOD)
    )
    make(fake).complete_json(MESSAGES, SCHEMA, "x")
    assert fake.models == ["m1", "m2"]


def test_repair_after_fallback_stays_on_fallback_model():
    fake = Fake(
        httpx.Response(404),
        completion("плохо", model="m2"),
        completion(GOOD, model="m2"),
    )
    response = make(fake).complete_json(MESSAGES, SCHEMA, "x")
    assert fake.models == ["m1", "m2", "m2"] and response.model == "m2"


# --- кэш -------------------------------------------------------------------------------------


def test_cache_hit_makes_zero_http_calls(tmp_path):
    fake = Fake(completion(GOOD))
    first = make(fake, cache_dir=tmp_path).complete_json(MESSAGES, SCHEMA, "x")
    assert first.cached is False
    assert len(list(tmp_path.rglob("*.json"))) == 1

    again = make(no_http, cache_dir=tmp_path)
    hit = again.complete_json(MESSAGES, SCHEMA, "x")
    assert hit.cached is True and hit.attempts == 0
    assert hit.data == first.data and hit.raw_text == GOOD and hit.model == "m1"
    assert again.stats == {"http_calls": 0, "cache_hits": 1, "repairs": 0}


def test_cache_applies_current_prepare(tmp_path):
    make(Fake(completion('{"answer": "ДА", "score": 1}')), cache_dir=tmp_path).complete_json(
        MESSAGES, SCHEMA, "x", prepare=lambda obj: {**obj, "answer": obj["answer"].lower()}
    )
    hit = make(no_http, cache_dir=tmp_path).complete_json(
        MESSAGES, SCHEMA, "x", prepare=lambda obj: {**obj, "answer": "нет"}
    )
    assert hit.data["answer"] == "нет"


def test_cache_key_depends_on_model_messages_schema_and_mode(tmp_path):
    base = make(Fake(), cache_dir=tmp_path)
    key = base.cache_key(MESSAGES, SCHEMA)
    assert key == make(Fake(), cache_dir=tmp_path).cache_key(MESSAGES, SCHEMA)
    assert key != make(Fake(), model="m9").cache_key(MESSAGES, SCHEMA)
    assert key != make(Fake(), structured_mode="json_schema").cache_key(MESSAGES, SCHEMA)
    assert key != base.cache_key(MESSAGES[:1], SCHEMA)
    assert key != base.cache_key(MESSAGES, {**SCHEMA, "required": ["answer"]})


def test_corrupt_cache_file_is_a_miss(tmp_path):
    client = make(Fake(completion(GOOD)), cache_dir=tmp_path)
    path = client._cache_path(client.cache_key(MESSAGES, SCHEMA))
    path.parent.mkdir(parents=True)
    path.write_text("{обрезано", encoding="utf-8")
    assert client.complete_json(MESSAGES, SCHEMA, "x").cached is False


# --- темп ------------------------------------------------------------------------------------


def test_min_interval_spaces_real_calls_process_wide(sleeps):
    clock = [100.0]
    fake = Fake(completion(GOOD), completion(GOOD), completion(GOOD))
    first = make(fake, sleeps, min_interval=3.0, clock=lambda: clock[0])
    second = make(fake, sleeps, min_interval=3.0, clock=lambda: clock[0])
    first.complete_json(MESSAGES, SCHEMA, "x")
    assert sleeps == []
    clock[0] += 1.0
    second.complete_json(MESSAGES[:1], SCHEMA, "x")  # другой экземпляр — тот же темп
    assert sleeps == [pytest.approx(2.0)]
    clock[0] += 10.0
    first.complete_json(MESSAGES[1:], SCHEMA, "x")
    assert len(sleeps) == 1


def test_burst_allows_short_series_then_waits(sleeps):
    clock = [0.0]
    fake = Fake(*[completion(GOOD) for _ in range(3)])
    client = make(fake, sleeps, min_interval=3.0, burst=2, clock=lambda: clock[0])
    for index in range(3):
        client.complete_json([{"role": "user", "content": str(index)}], SCHEMA, "x")
    assert sleeps == [pytest.approx(6.0)]


def test_cache_hits_do_not_consume_rate(tmp_path, sleeps):
    clock = [0.0]
    make(Fake(completion(GOOD)), cache_dir=tmp_path).complete_json(MESSAGES, SCHEMA, "x")
    client = make(no_http, sleeps, cache_dir=tmp_path, min_interval=5.0, clock=lambda: clock[0])
    for _ in range(3):
        client.complete_json(MESSAGES, SCHEMA, "x")
    assert sleeps == []


# --- журнал и фабрика -----------------------------------------------------------------------


def test_real_call_is_logged_at_info(caplog):
    caplog.set_level(logging.INFO, logger=L.__name__)
    make(Fake(completion(GOOD))).complete_json(MESSAGES, SCHEMA, "select_family")
    record = next(record for record in caplog.records if "select_family" in record.getMessage())
    assert record.levelno == logging.INFO
    assert "m1" in record.getMessage() and "100→20" in record.getMessage()


def test_get_llm_client_from_config(monkeypatch, tmp_path):
    from apps.ste import config

    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://openrouter.test/api/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(config, "LLM_CACHE_DIR", tmp_path)
    assert L.get_llm_client() is None

    monkeypatch.setattr(config, "LLM_BASE_URL", "http://localhost:11434/v1")
    local = L.get_llm_client()
    assert local is not None and local.base_url == "http://localhost:11434/v1"

    monkeypatch.setattr(config, "LLM_BASE_URL", "https://openrouter.test/api/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-real")
    client = L.get_llm_client(timeout=7.0, min_interval=0.0)
    assert client.model == config.LLM_MODEL
    assert client.models == [config.LLM_MODEL, *config.LLM_FALLBACK_MODELS]
    assert client.structured_mode == config.LLM_STRUCTURED and client.cache_dir == tmp_path

    monkeypatch.setattr(config, "LLM_ENABLED", False)
    assert L.get_llm_client() is None
