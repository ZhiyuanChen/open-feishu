from __future__ import annotations

from typing import Any

from ..context import current_tool_context
from ..memory import MemoryIdentityConflict, MemoryIdentityStore, MemoryScope, MemoryStore
from ..result import ToolOutcome, ToolResult
from ..tools import Tool


def recall_memory(*, description: str, name: str = "recall_memory") -> Tool:
    r"""Create an explicit, read-only search tool for the requester's memories."""

    async def handler(*, query: str, max_items: int = 12) -> ToolResult:
        if not isinstance(query, str):
            return ToolResult(ToolOutcome.FAILED, content="memory query must be a string", is_error=True)
        if len(query) > 200:
            return ToolResult(ToolOutcome.FAILED, content="memory query exceeds 200 characters", is_error=True)
        if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 50:
            return ToolResult(ToolOutcome.FAILED, content="max_items must be an integer from 1 to 50", is_error=True)
        try:
            store, namespace, owner_key = await _memory_context()
        except MemoryIdentityConflict:
            return _memory_identity_conflict()
        if store is None or namespace is None:
            return _memory_not_configured()
        text = query.strip()
        if not text:
            return ToolResult(ToolOutcome.FAILED, content="memory query must not be empty", is_error=True)
        records = await store.recall(namespace=namespace, owner_key=owner_key, query=text, limit=max_items)
        return ToolResult(
            ToolOutcome.COMPLETED,
            content={"memories": [{"scope": record.scope, "content": record.content} for record in records]},
        )

    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "max_items": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=handler,
    )


def remember_memory(*, description: str, name: str = "remember_memory") -> Tool:
    r"""Create a confirmation-gated tool that stores one bounded user or project memory."""

    async def handler(*, scope: str, content: str) -> ToolResult:
        try:
            store, namespace, owner_key = await _memory_context()
        except MemoryIdentityConflict:
            return _memory_identity_conflict()
        if store is None or namespace is None:
            return _memory_not_configured()
        text = content.strip()
        if not text:
            return ToolResult(ToolOutcome.FAILED, content="memory content must not be empty", is_error=True)
        if len(text) > 1000:
            return ToolResult(ToolOutcome.FAILED, content="memory content exceeds 1000 characters", is_error=True)
        if scope not in ("project", "user"):
            return ToolResult(ToolOutcome.FAILED, content="memory scope must be 'user' or 'project'", is_error=True)
        if scope == "user" and owner_key is None:
            return ToolResult(ToolOutcome.FAILED, content="requesting user could not be identified", is_error=True)
        memory_scope: MemoryScope = "user" if scope == "user" else "project"
        record = await store.remember(
            namespace=namespace,
            scope=memory_scope,
            owner_key=owner_key,
            content=text,
        )
        return ToolResult(ToolOutcome.COMPLETED, content={"memory_id": record.memory_id, "scope": record.scope})

    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["user", "project"]},
                "content": {"type": "string", "maxLength": 1000},
            },
            "required": ["scope", "content"],
            "additionalProperties": False,
        },
        handler=handler,
        requires_approval=True,
    )


async def _memory_context() -> tuple[MemoryStore | None, str | None, str | None]:
    context = current_tool_context()
    store = context.memory_store
    namespace = context.memory_namespace
    user = context.requesting_user()
    owner_keys = _owner_keys(user)
    owner_key = owner_keys[0] if owner_keys else None
    if namespace is not None and isinstance(store, MemoryIdentityStore):
        owner_key = await store.resolve_owner(namespace=namespace, owner_keys=owner_keys)
    return store, namespace, owner_key


def _owner_keys(user: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        f"{kind}:{value}"
        for kind in ("user_id", "open_id", "union_id")
        if isinstance(value := user.get(kind), str) and value
    )


def _memory_not_configured() -> ToolResult:
    return ToolResult(ToolOutcome.BLOCKED, content="memory is not configured for this agent", is_error=True)


def _memory_identity_conflict() -> ToolResult:
    return ToolResult(ToolOutcome.BLOCKED, content="requesting user identity aliases conflict", is_error=True)
