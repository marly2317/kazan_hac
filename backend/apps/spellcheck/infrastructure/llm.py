"""The language-model stage, over any OpenAI-compatible endpoint.

Reached only by queries the deterministic stages could not improve: word boundaries that moved
(``фотобумага4`` → ``фотобумага 4``), two words run together, a transliterated brand, a phrase
that is misspelled in a way no single-token edit explains.

**The hackathon rules forbid proprietary LLM APIs** — OpenAI GPT, Google Gemini, Anthropic
Claude "и другие подобные сервисы" — and allow only open models, with a combined inference
budget of 40 GB VRAM and an explicit preference for light, fast ones. LLM routers are allowed,
with a stated preference for Russian providers (timeweb, selectel).

So this client names no model and no vendor. It speaks the OpenAI-compatible chat API that
vLLM, Ollama, timeweb and selectel all expose, and both the base URL and the model come from
configuration. It is **disabled by default**: the deterministic stages carry the feature, which
is also what the rules reward — "предпочтение будет отдаваться легковесным решениям с высокой
скоростью инференса".

Three properties matter more than the prompt:

* **It is optional.** No API key, a timeout, a 429, a malformed answer — the search still runs,
  on the user's own words. Correction is an enhancement, never a dependency.
* **The output is constrained.** The model is asked for one line and nothing else, and the
  answer is rejected unless it looks like a plausible rewrite of the input. A model that decides
  to answer the question instead of fixing it must not reach the search box.
* **It is cached.** The same misspelling recurs across users, and a cache hit costs nothing.
"""

from __future__ import annotations

import logging

import httpx

from apps.spellcheck.application.ports import CorrectionCache
from apps.spellcheck.domain.correction import MAX_QUERY_LENGTH

logger = logging.getLogger(__name__)

#: Appended to the configured base URL, which is expected to end at ``/v1``.
COMPLETIONS_PATH = "/chat/completions"

SYSTEM_PROMPT = """\
Ты — автокорректор поисковых запросов интернет-магазина. Запросы на русском языке.

Исправь ТОЛЬКО опечатки, раскладку клавиатуры и границы слов. Верни исправленный запрос \
одной строкой, без кавычек, без пояснений, без знаков препинания в конце.

Запрещено:
- отвечать на запрос, дополнять или расширять его;
- менять смысл, число, единицы измерения, артикулы и числа;
- переводить слова на другой язык;
- добавлять слова, которых не было.

Если запрос написан правильно — верни его дословно без изменений."""


class LlmRewriter:
    """Last stage of the pipeline. Disabled, not failing, when no endpoint is configured."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        cache: CorrectionCache,
        api_key: str = "",
        timeout: float = 4.0,
        disable_reasoning: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._cache = cache
        # Optional: a self-hosted vLLM or Ollama usually needs no key at all.
        self._api_key = api_key
        self._timeout = timeout
        self._disable_reasoning = disable_reasoning
        # One pooled client for the life of the process. A fresh `httpx.post` per call opens a
        # new TCP connection and repeats the TLS handshake every time — which is what made the
        # first real request time out against a remote endpoint. Connecting is also given its
        # own, longer budget than reading: the handshake is the slow part, and counting it
        # against the read timeout is what turned a working endpoint into a dead one.
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout, connect=max(timeout, 10.0)),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )

    @property
    def enabled(self) -> bool:
        """A base URL *and* a model. Either alone cannot address an endpoint."""
        return bool(self._base_url and self._model)

    def rewrite(self, query: str) -> str | None:
        if not self.enabled:
            return None

        cache_key = f"{self._model}:{query.casefold()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            # An empty string is the cached form of "the model had nothing to change", so the
            # same clean query does not pay for a request on every search.
            return cached or None

        answer = self._ask(query)
        if answer is None:
            # The request failed. Nothing is cached: a transient timeout must not turn into a
            # week of silently skipping this query.
            return None

        accepted = answer if answer and _plausible(query, answer) else ""
        if answer and not accepted:
            logger.warning("Rejected implausible LLM correction: %r -> %r", query, answer)

        self._cache.set(cache_key, accepted)
        return accepted or None

    def _ask(self, query: str) -> str | None:
        """The model's answer, or ``None`` when the request did not complete.

        ``None`` and ``""`` mean different things and the caller depends on it: an empty string
        is the model saying "nothing to change", which is worth caching, while ``None`` is a
        failure, which must not be — a single timeout would otherwise suppress corrections for
        that query for as long as the cache lives.
        """
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            # Deterministic: the same misspelling should not get two different fixes, and the
            # cache would otherwise hold whichever one happened to come back first.
            "temperature": 0,
            # A corrected query cannot be much longer than the original; this is the ceiling
            # that stops a model that decided to write an essay. Generous enough that a model
            # which emits a few stray tokens before the answer still finishes it.
            "max_tokens": 256,
        }

        if self._disable_reasoning:
            # Hybrid reasoning models (Qwen3, DeepSeek-R1 derivatives) think before answering,
            # and the thinking counts against `max_tokens`. Left on, a short budget is spent
            # entirely on reasoning tokens: the response comes back `finish_reason: "length"`
            # with `content: null` and the correction is silently lost. Fixing a typo needs no
            # deliberation, so it is turned off — which also removes most of the latency and
            # cost. Servers that do not know the field ignore it; set
            # `LLM_DISABLE_REASONING=0` for one that rejects unknown parameters instead.
            payload["reasoning"] = {"enabled": False}

        try:
            response = self._client.post(COMPLETIONS_PATH, json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            logger.warning("LLM request failed: %s", exc)
            return None
        except ValueError as exc:
            logger.warning("LLM returned a non-JSON body: %s", exc)
            return None

        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            logger.warning("Unexpected LLM payload: %s", body)
            return None

        if not content and choice.get("finish_reason") == "length":
            # Almost always a reasoning model that spent the whole budget thinking.
            logger.warning(
                "LLM answer truncated before any content; "
                "raise max_tokens or disable reasoning for model %s",
                self._model,
            )
            return None

        return _clean(content or "")


def _clean(content: str) -> str:
    """Strip the wrapping a chat model adds even when told not to."""
    text = " ".join(content.strip().split())
    # A single pair of surrounding quotes — the most common instruction-following slip.
    for opening, closing in (('"', '"'), ("'", "'"), ("«", "»"), ("“", "”")):
        if text.startswith(opening) and text.endswith(closing) and len(text) > 1:
            text = text[1:-1].strip()
    return text.rstrip(".")


def _plausible(original: str, answer: str) -> bool:
    """Guard against a model that answered the query instead of correcting it.

    A correction keeps roughly the shape of the input. Anything that grew a lot, collapsed to
    nothing, or came back as several lines is not a corrected search query.
    """
    if not answer or len(answer) > MAX_QUERY_LENGTH:
        return False
    if "\n" in answer:
        return False
    # Generous, because a legitimate fix may split one run-on token into three words — but far
    # short of a model that started explaining itself.
    return len(answer) <= max(len(original) * 2, len(original) + 20)
