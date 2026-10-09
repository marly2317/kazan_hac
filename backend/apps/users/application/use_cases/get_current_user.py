"""Load the account behind the current session."""

from __future__ import annotations

from apps.users.application.dto import GetCurrentUserInput
from apps.users.application.ports import UserRepository
from apps.users.domain.entities import User
from apps.users.domain.exceptions import UserNotFound


class GetCurrentUser:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    def execute(self, data: GetCurrentUserInput) -> User:
        user = self._users.get_by_id(data.user_id)
        if user is None:
            raise UserNotFound()
        return user
