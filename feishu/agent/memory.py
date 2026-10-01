from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

MemoryScope = Literal["project", "user"]


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
