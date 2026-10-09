"""The use-case contract.

A use case takes one input dataclass and returns one domain value. It depends on ports
(``typing.Protocol``) only — never on Django, DRF or an ORM model — which is what lets the tests
drive it with in-memory doubles.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

TInput = TypeVar("TInput", contravariant=True)
TOutput = TypeVar("TOutput", covariant=True)


class UseCase(Protocol[TInput, TOutput]):
    def execute(self, data: TInput) -> TOutput: ...
