from __future__ import annotations

from types import SimpleNamespace

import pytest

from feishu.agent.context import ToolContext, use_tool_context
from feishu.agent.scheduled import ScheduledTaskStore
from feishu.agent.toolkit.scheduled import register_scheduled_tools
from feishu.agent.tools import ToolRegistry


async def test_owner_manages_schedule(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedules.db")
    registry = ToolRegistry()
    register_scheduled_tools(registry, store, clock=lambda: 1)
    event = SimpleNamespace(tenant_key="tenant-a", body={"message": {"chat_id": "chat"}})
    with use_tool_context(ToolContext(event=event, user={"user_id": "alice"})):
        assert registry.get("create_scheduled_task").requires_approval
        created = await registry.dispatch(
            "create_scheduled_task",
            {
                "instruction": "check",
                "schedule": {"kind": "once", "run_at": 100},
                "timezone": "UTC",
                "policy": "read_only",
            },
        )
        task_id = created.content["task_id"]
        listed = await registry.dispatch("list_scheduled_tasks", {})
        assert [task["task_id"] for task in listed.content] == [task_id]
        task = store.get(task_id)
        assert registry.get("pause_scheduled_task").requires_approval
        await registry.dispatch("pause_scheduled_task", {"task_id": task_id, "revision": task.revision})
    other = SimpleNamespace(tenant_key="tenant-b", body={"message": {"chat_id": "chat"}})
    with use_tool_context(ToolContext(event=other, user={"user_id": "alice"})):
        assert (await registry.dispatch("list_scheduled_tasks", {})).content == []
        with pytest.raises(PermissionError):
            await registry.dispatch("resume_scheduled_task", {"task_id": task_id, "revision": task.revision + 1})
