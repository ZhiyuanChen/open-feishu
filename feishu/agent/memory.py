from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

MemoryScope = Literal["project", "user"]


class MemoryIdentityConflict(ValueError):
    """Trusted caller aliases refer to more than one established memory owner."""


@dataclass(frozen=True)
class MemoryRecord:
    """A bounded durable fact owned by one user or shared within one project namespace."""

    memory_id: str
    namespace: str
    scope: MemoryScope
    owner_key: str | None
    content: str
    created_at: int
    updated_at: int
    expires_at: int = 0


@runtime_checkable
class MemoryStore(Protocol):
    async def remember(
        self,
        *,
        namespace: str,
        scope: MemoryScope,
        owner_key: str | None,
        content: str,
        expires_at: int = 0,
    ) -> MemoryRecord: ...

    async def recall(self, *, namespace: str, owner_key: str | None, query: str, limit: int = 12) -> list[MemoryRecord]:
        """Return memories matching an explicit query within the caller's scope."""
        ...


@runtime_checkable
class MemoryIdentityStore(MemoryStore, Protocol):
    """Optional extension for durable links between trusted identifiers of the same user."""

    async def resolve_owner(self, *, namespace: str, owner_keys: tuple[str, ...]) -> str | None:
        """Link caller identifiers within one namespace, rejecting conflicting established owners."""
        ...
