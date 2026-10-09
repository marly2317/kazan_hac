"""Django looks for ``<app>.models``; the real models live in the infrastructure layer.

Re-exported here so ``makemigrations`` and the app registry find them without any module
importing across a layer boundary it should not.
"""

from apps.users.infrastructure.models import User

__all__ = ["User"]
