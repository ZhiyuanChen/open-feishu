from __future__ import annotations

from feishu.agent.context import ToolContext, use_tool_context
from feishu.agent.persistence import SqliteMemoryStore
from feishu.agent.result import ToolOutcome
from feishu.agent.toolkit.memory import recall_memory, remember_memory


async def test_memory_tools_scope_writes_to_the_requesting_user(tmp_path) -> None:
    store = SqliteMemoryStore(tmp_path / "agent.db")
    remember = remember_memory(description="remember")
    recall = recall_memory(description="recall")

    assert remember.requires_approval is True
    with use_tool_context(ToolContext(memory_store=store, memory_namespace="oncall", user={"user_id": "alice"})):
        result = await remember.handler(scope="user", content="Prefer concise status reports.")
        records = await recall.handler(query="concise")

    assert result.outcome is ToolOutcome.COMPLETED
    assert records.content["memories"] == [{"scope": "user", "content": "Prefer concise status reports."}]
