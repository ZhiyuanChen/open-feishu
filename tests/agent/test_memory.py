from __future__ import annotations

from feishu.agent.loop import AgentEngine
from feishu.agent.memory import MemoryRecord
from feishu.agent.persistence import SqliteMemoryStore
from feishu.agent.tools import ToolRegistry
from feishu.events.envelope import Event
from tests._fakes import FakeLlmBackend


async def test_sqlite_memory_store_scopes_project_and_user_records(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "agent.db")
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


async def test_agent_turn_context_does_not_automatically_include_persisted_memory(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "agent.db")
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
