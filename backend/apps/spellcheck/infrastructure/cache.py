"""Django's cache framework behind the ``CorrectionCache`` port."""

from __future__ import annotations

import hashlib

from django.core.cache import cache as django_cache

#: A week: product names change far more slowly than this, and a wrong entry is a cosmetic
#: problem, not a correctness one.
DEFAULT_TTL = 60 * 60 * 24 * 7


class DjangoCorrectionCache:
    def __init__(self, *, prefix: str = "spellcheck", ttl: int = DEFAULT_TTL) -> None:
        self._prefix = prefix
        self._ttl = ttl

    def _key(self, key: str) -> str:
        # Hashed: a raw query can exceed the key length some cache backends accept, and may
        # contain characters memcached rejects outright.
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return f"{self._prefix}:{digest}"

    def get(self, key: str) -> str | None:
        return django_cache.get(self._key(key))

    def set(self, key: str, value: str) -> None:
        django_cache.set(self._key(key), value, self._ttl)
