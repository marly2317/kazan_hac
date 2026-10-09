"""User entity and its value objects. No Django import lives in this layer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from apps.users.domain.enums import Role
from apps.users.domain.exceptions import InvalidEmail, InvalidFullName

# Deliberately permissive: real deliverability is proven by sending mail, not by a regex.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

MAX_EMAIL_LENGTH = 254
MAX_FULL_NAME_LENGTH = 200


def normalize_email(raw: str) -> str:
    """Trim, lowercase and validate the shape.

    RFC 5321 makes the local part case-sensitive, but no mail provider in practice treats
    ``User@`` and ``user@`` as two mailboxes. Lowercasing the whole address is what keeps the
    unique index meaningful: otherwise the same person registers twice by changing one letter,
    and login silently misses the account they created.
    """
    if not isinstance(raw, str):
        raise InvalidEmail()
    candidate = raw.strip()
    if len(candidate) > MAX_EMAIL_LENGTH or not _EMAIL_RE.match(candidate):
        raise InvalidEmail()
    return candidate.lower()


def normalize_full_name(raw: str) -> str:
    """Collapse whitespace and require something non-empty to display in the header."""
    if not isinstance(raw, str):
        raise InvalidFullName()
    candidate = " ".join(raw.split())
    if not candidate or len(candidate) > MAX_FULL_NAME_LENGTH:
        raise InvalidFullName()
    return candidate


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime

    @property
    def initials(self) -> str:
        """Up to two letters for the header avatar. Derived here, not in the UI, so the API and
        the admin agree with the SPA on what a user is called."""
        parts = [part for part in self.full_name.split() if part]
        return "".join(part[0].upper() for part in parts[:2]) or self.email[:1].upper()

    def has_role(self, *roles: Role) -> bool:
        return self.role in roles
