"""Хранилище состояния сессий: Postgres в работе, словарь в тестах."""

from __future__ import annotations

import uuid
from typing import Protocol


class SessionStore(Protocol):
    def load(self, session_id: str) -> dict | None: ...

    def save(self, session_id: str | None, data: dict) -> str: ...


class MemorySessionStore:
    def __init__(self) -> None:
        self._data: dict[str, dict] = {}

    def load(self, session_id: str) -> dict | None:
        return self._data.get(session_id)

    def save(self, session_id: str | None, data: dict) -> str:
        session_id = session_id or str(uuid.uuid4())
        self._data[session_id] = data
        return session_id


class DjangoSessionStore:
    def load(self, session_id: str) -> dict | None:
        from apps.ste.infrastructure.models import SteSession

        try:
            return SteSession.objects.get(pk=uuid.UUID(str(session_id))).data
        except (SteSession.DoesNotExist, ValueError):
            return None

    def save(self, session_id: str | None, data: dict) -> str:
        from apps.ste.infrastructure.models import SteSession

        if session_id:
            try:
                key = uuid.UUID(str(session_id))
            except ValueError:
                key = uuid.uuid4()
        else:
            key = uuid.uuid4()
        SteSession.objects.update_or_create(pk=key, defaults={"data": data})
        return str(key)
