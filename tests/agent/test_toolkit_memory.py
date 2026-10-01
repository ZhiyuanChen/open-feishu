from __future__ import annotations

from types import SimpleNamespace

import pytest

from feishu.agent.context import ToolContext, use_tool_context
from feishu.agent.persistence import SqliteMemoryStore
from feishu.agent.result import ToolOutcome
from feishu.agent.toolkit.memory import recall_memory, remember_memory
from feishu.agent.tools import ToolRegistry, ToolValidationError


@pytest.fixture
def store(tmp_path, request):
    store = SqliteMemoryStore(tmp_path / "agent.db")
    request.addfinalizer(store._db.close)
    return store


async def test_memory_tools_scope_writes_to_the_requesting_user(store) -> None:
    remember = remember_memory(description="remember")
    recall = recall_memory(description="recall")

    assert remember.requires_approval is True
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall", user={"user_id": "alice"})):
        result = await remember.handler(scope="user", content="Prefer concise status reports.")
        records = await recall.handler(query="concise")

    assert result.outcome is ToolOutcome.COMPLETED
    assert records.content["memories"] == [{"scope": "user", "content": "Prefer concise status reports."}]


@pytest.mark.parametrize(
    ("first_user", "later_user"),
    [
        ({"open_id": "ou_alice"}, {"open_id": "ou_alice", "user_id": "alice"}),
        ({"open_id": "ou_alice", "user_id": "alice"}, {"open_id": "ou_alice"}),
        ({"union_id": "on_alice"}, {"union_id": "on_alice", "user_id": "alice"}),
        ({"union_id": "on_alice", "user_id": "alice"}, {"union_id": "on_alice"}),
    ],
)
async def test_memory_remains_visible_when_requesting_identity_subset_changes(store, first_user, later_user) -> None:
    remember = remember_memory(description="remember")
    recall = recall_memory(description="recall")
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall", user=first_user)):
        await remember.handler(scope="user", content="Prefer concise status reports.")
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall", user=later_user)):
        result = await recall.handler(query="concise")

    assert result.content["memories"] == [{"scope": "user", "content": "Prefer concise status reports."}]


async def test_linked_identity_aliases_survive_reopening_without_exposing_other_users_or_projects(
    tmp_path, request, store
) -> None:
    path = tmp_path / "agent.db"
    await store.remember(namespace="oncall", scope="user", owner_key="open_id:ou_alice", content="Alice memory.")
    await store.remember(namespace="oncall", scope="user", owner_key="user_id:alice", content="Alice legacy memory.")
    await store.remember(namespace="oncall", scope="user", owner_key="union_id:on_alice", content="Alice union memory.")
    await store.remember(namespace="oncall", scope="user", owner_key="user_id:bob", content="Bob memory.")
    await store.remember(namespace="other", scope="user", owner_key="user_id:alice", content="Other project memory.")
    recall = recall_memory(description="recall")
    with use_tool_context(
        ToolContext(
            memory_store=store,
            memory_namespace="oncall",
            user={"user_id": "alice", "open_id": "ou_alice", "union_id": "on_alice"},
        )
    ):
        initial = await recall.handler(query="memory")
    assert {record["content"] for record in initial.content["memories"]} == {
        "Alice memory.",
        "Alice legacy memory.",
        "Alice union memory.",
    }
    store._db.close()

    reopened = SqliteMemoryStore(path)
    request.addfinalizer(reopened._db.close)
    with use_tool_context(ToolContext(memory_store=reopened, memory_namespace="oncall", user={"union_id": "on_alice"})):
        result = await recall.handler(query="memory")
    assert {record["content"] for record in result.content["memories"]} == {
        "Alice memory.",
        "Alice legacy memory.",
        "Alice union memory.",
    }
    with use_tool_context(ToolContext(memory_store=reopened, memory_namespace="oncall", user={"user_id": "bob"})):
        bob = await recall.handler(query="memory")
    assert bob.content["memories"] == [{"scope": "user", "content": "Bob memory."}]
    with use_tool_context(ToolContext(memory_store=reopened, memory_namespace="other", user={"open_id": "ou_alice"})):
        other = await recall.handler(query="memory")
    assert other.content["memories"] == []


@pytest.mark.parametrize("max_items", [0, -1, 51, 60])
async def test_recall_dispatch_rejects_counts_outside_declared_limits(store, max_items) -> None:
    for index in range(60):
        await store.remember(namespace="oncall", scope="project", owner_key=None, content=f"Memory {index}.")
    registry = ToolRegistry()
    registry.add(recall_memory(description="recall"))
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall")):
        result = await registry.dispatch("recall_memory", {"query": "Memory", "max_items": max_items})

    assert result.outcome is ToolOutcome.FAILED
    assert result.is_error is True


async def test_recall_dispatch_rejects_query_longer_than_declared_limit(store) -> None:
    text = "x" * 201
    await store.remember(namespace="oncall", scope="project", owner_key=None, content=text)
    registry = ToolRegistry()
    registry.add(recall_memory(description="recall"))
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall")):
        result = await registry.dispatch("recall_memory", {"query": text})

    assert result.outcome is ToolOutcome.FAILED
    assert result.is_error is True


@pytest.mark.parametrize("max_items", [True, False])
async def test_recall_handler_rejects_boolean_count(store, max_items) -> None:
    await store.remember(namespace="oncall", scope="project", owner_key=None, content="Memory.")
    recall = recall_memory(description="recall")
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall")):
        result = await recall.handler(query="Memory", max_items=max_items)

    assert result.outcome is ToolOutcome.FAILED
    assert result.is_error is True


@pytest.mark.parametrize("max_items", [True, False])
async def test_recall_dispatch_rejects_boolean_count(store, max_items) -> None:
    registry = ToolRegistry()
    registry.add(recall_memory(description="recall"))
    with (
        use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall")),
        pytest.raises(ToolValidationError),
    ):
        await registry.dispatch("recall_memory", {"query": "Memory", "max_items": max_items})


async def test_memory_tools_block_conflicting_known_identities_without_linking_their_records(store) -> None:
    remember = remember_memory(description="remember")
    recall = recall_memory(description="recall")
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall", user={"user_id": "alice"})):
        await remember.handler(scope="user", content="Alice memory.")
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall", user={"open_id": "ou_bob"})):
        await remember.handler(scope="user", content="Bob memory.")

    with use_tool_context(
        ToolContext(memory_store=store, memory_namespace="oncall", user={"user_id": "alice", "open_id": "ou_bob"})
    ):
        read = await recall.handler(query="memory")
        write = await remember.handler(scope="user", content="Conflicting memory.")
    assert read.outcome is ToolOutcome.BLOCKED
    assert read.is_error is True
    assert write.outcome is ToolOutcome.BLOCKED
    assert write.is_error is True
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall", user={"user_id": "alice"})):
        alice = await recall.handler(query="memory")
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall", user={"open_id": "ou_bob"})):
        bob = await recall.handler(query="memory")
    assert alice.content["memories"] == [{"scope": "user", "content": "Alice memory."}]
    assert bob.content["memories"] == [{"scope": "user", "content": "Bob memory."}]


@pytest.mark.parametrize("max_items", [1, 50])
async def test_recall_dispatch_accepts_declared_count_and_query_boundaries(store, max_items) -> None:
    text = "x" * 200
    for index in range(60):
        await store.remember(namespace="oncall", scope="project", owner_key=None, content=f"{text} {index}")
    registry = ToolRegistry()
    registry.add(recall_memory(description="recall"))
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall")):
        result = await registry.dispatch("recall_memory", {"query": text, "max_items": max_items})

    assert result.outcome is ToolOutcome.COMPLETED
    assert len(result.content["memories"]) == max_items


async def test_memory_tools_keep_legacy_memory_store_method_signatures(store) -> None:
    legacy_store = SimpleNamespace(remember=store.remember, recall=store.recall)
    remember = remember_memory(description="remember")
    recall = recall_memory(description="recall")
    with use_tool_context(
        ToolContext(
            memory_store=legacy_store, memory_namespace="oncall", user={"user_id": "alice", "open_id": "ou_alice"}
        )
    ):
        written = await remember.handler(scope="user", content="Prefer concise status reports.")
        result = await recall.handler(query="concise")

    assert written.outcome is ToolOutcome.COMPLETED
    assert result.content["memories"] == [{"scope": "user", "content": "Prefer concise status reports."}]
