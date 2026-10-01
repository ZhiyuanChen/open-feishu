from __future__ import annotations

import sqlite3

import pytest

from feishu.agent.loop import AgentEngine
from feishu.agent.memory import MemoryRecord
from feishu.agent.persistence import SqliteMemoryStore
from feishu.agent.tools import ToolRegistry
from feishu.events.envelope import Event
from tests._fakes import FakeLlmBackend


async def test_sqlite_memory_store_scopes_project_and_user_records(tmp_path, request) -> None:
    store = SqliteMemoryStore(tmp_path / "agent.db")
    request.addfinalizer(store._db.close)
    await store.remember(
        namespace="oncall", scope="project", owner_key=None, content="Production alerts use P1 escalation."
    )
    await store.remember(
        namespace="oncall", scope="user", owner_key="user_id:alice", content="Alice prefers concise replies."
    )
    await store.remember(namespace="oncall", scope="user", owner_key="user_id:bob", content="Bob prefers English.")

    records = await store.recall(namespace="oncall", owner_key="user_id:alice", query="prefers")

    assert [record.content for record in records] == ["Alice prefers concise replies."]
    assert all(isinstance(record, MemoryRecord) for record in records)

    project_records = await store.recall(namespace="oncall", owner_key="user_id:alice", query="P1")

    assert [record.content for record in project_records] == ["Production alerts use P1 escalation."]


async def test_agent_turn_context_does_not_automatically_include_persisted_memory(tmp_path, request) -> None:
    store = SqliteMemoryStore(tmp_path / "agent.db")
    request.addfinalizer(store._db.close)
    await store.remember(namespace="oncall", scope="project", owner_key=None, content="Escalate P1 incidents.")
    await store.remember(namespace="oncall", scope="user", owner_key="user_id:alice", content="Use concise Chinese.")
    agent = AgentEngine(
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
        memory_store=store,
        memory_namespace="oncall",
    )
    event = Event.from_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1", "event_id": "memory_1"},
            "event": {"sender": {"sender_id": {"user_id": "alice"}}},
        }
    )

    context = await agent._turn_context_for_event(event, None)

    assert context is None


async def test_sqlite_memory_store_does_not_link_already_distinct_owners(tmp_path, request) -> None:
    store = SqliteMemoryStore(tmp_path / "agent.db")
    request.addfinalizer(store._db.close)
    await store.remember(namespace="oncall", scope="user", owner_key="user_id:alice", content="Alice user memory.")
    await store.remember(namespace="oncall", scope="user", owner_key="open_id:ou_alice", content="Alice open memory.")
    await store.remember(namespace="other", scope="user", owner_key="user_id:alice", content="Other namespace memory.")
    await store.resolve_owner(namespace="oncall", owner_keys=("open_id:ou_alice",))
    await store.resolve_owner(namespace="oncall", owner_keys=("user_id:alice",))

    with pytest.raises(ValueError, match="conflict"):
        await store.resolve_owner(namespace="oncall", owner_keys=("user_id:alice", "open_id:ou_alice"))
    alice = await store.recall(namespace="oncall", owner_key="open_id:ou_alice", query="memory")
    other = await store.recall(namespace="other", owner_key="open_id:ou_alice", query="memory")
    anonymous = await store.recall(namespace="oncall", owner_key=None, query="memory")

    assert [record.content for record in alice] == ["Alice open memory."]
    assert other == []
    assert anonymous == []


async def test_sqlite_memory_store_preserves_established_owner_when_aliases_are_added_or_reordered(
    tmp_path, request
) -> None:
    store = SqliteMemoryStore(tmp_path / "agent.db")
    request.addfinalizer(store._db.close)
    assert await store.resolve_owner(namespace="oncall", owner_keys=("open_id:ou_alice",)) == "open_id:ou_alice"

    added = await store.resolve_owner(namespace="oncall", owner_keys=("user_id:alice", "open_id:ou_alice"))
    reordered = await store.resolve_owner(namespace="oncall", owner_keys=("open_id:ou_alice", "user_id:alice"))
    reduced = await store.resolve_owner(namespace="oncall", owner_keys=("user_id:alice",))

    assert added == "open_id:ou_alice"
    assert reordered == "open_id:ou_alice"
    assert reduced == "open_id:ou_alice"


async def test_sqlite_memory_store_reopens_legacy_rows_without_an_alias_table(tmp_path, request) -> None:
    path = tmp_path / "agent.db"
    with sqlite3.connect(path) as legacy:
        request.addfinalizer(legacy.close)
        legacy.execute(
            "CREATE TABLE memories (memory_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, scope TEXT NOT NULL, "
            "owner_key TEXT, content TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
            "expires_at INTEGER NOT NULL)"
        )
        legacy.executemany(
            "INSERT INTO memories VALUES (?, 'oncall', 'user', ?, ?, 1, 1, 0)",
            [
                ("m_open", "open_id:ou_alice", "Open memory."),
                ("m_user", "user_id:alice", "User memory."),
                ("m_union", "union_id:on_alice", "Union memory."),
                ("m_bob", "user_id:bob", "Bob memory."),
            ],
        )
    store = SqliteMemoryStore(path)
    request.addfinalizer(store._db.close)
    await store.resolve_owner(namespace="oncall", owner_keys=("user_id:alice", "open_id:ou_alice", "union_id:on_alice"))

    records = await store.recall(namespace="oncall", owner_key="union_id:on_alice", query="memory")

    assert {record.content for record in records} == {"Open memory.", "User memory.", "Union memory."}
