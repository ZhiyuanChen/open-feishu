import asyncio
from types import SimpleNamespace

import pytest

from feishu.agent.context import ToolContext, use_tool_context
from feishu.agent.scheduled_policy import (
    ScheduledPolicyRegistry,
    ScheduledToolBlocked,
    current_scheduled_registry,
    scheduled_registry_scope,
)
from feishu.agent.toolkit._base import reauth_on_permission_error
from feishu.agent.toolkit.calendar import list_calendar_events
from feishu.agent.tools import ToolRegistry


@pytest.fixture
def registry():
    registry = ToolRegistry()
    for name, effect in (("read", "read"), ("write", "write"), ("unknown", None)):
        registry.register(
            name, lambda: "ok", input_schema={"type": "object"}, description=name, scheduled_effect=effect
        )
    return registry


class TestReadOnly:
    def test_exposes_reads(self, registry):
        policy = ScheduledPolicyRegistry(registry, "read_only")
        assert [spec.name for spec in policy.specs()] == ["read"]
        assert policy.get("read").requires_approval is False

    @pytest.mark.parametrize("name", ["write", "unknown"])
    async def test_rejects_writes(self, registry, name):
        policy = ScheduledPolicyRegistry(registry, "read_only")
        with pytest.raises(KeyError):
            policy.get(name)
        with pytest.raises(ScheduledToolBlocked):
            await policy.dispatch(name, {})


class TestConfirmWrites:
    @pytest.mark.parametrize("name", ["write", "unknown"])
    async def test_requires_trusted_approval(self, registry, name):
        policy = ScheduledPolicyRegistry(registry, "confirm_writes")
        assert name in [spec.name for spec in policy.specs()]
        assert policy.get(name).requires_approval is True
        with pytest.raises(ScheduledToolBlocked):
            await policy.dispatch(name, {})
        assert registry.get(name).requires_approval is False

    async def test_read_can_run(self, registry):
        policy = ScheduledPolicyRegistry(registry, "confirm_writes")
        assert (await policy.dispatch("read", {})).content == "ok"


async def test_wrapped_calendar_read():
    async def list_events(calendar_id, **kwargs):
        return [calendar_id]

    client = SimpleNamespace(calendar=SimpleNamespace(events=SimpleNamespace(instance_view=list_events)))
    tool = list_calendar_events(description="calendar", as_user=False)
    registry = ToolRegistry()
    registry.add(reauth_on_permission_error(tool, ("calendar:read",)))
    policy = ScheduledPolicyRegistry(registry, "read_only")
    with use_tool_context(ToolContext(client=client)):
        result = await policy.dispatch(
            tool.name, {"calendar_id": "cal_1", "time_min": "2026-01-01", "time_max": "2026-01-02"}
        )
    assert result.content == ["cal_1"]


async def test_scope_is_task_local(registry):
    async def scheduled():
        with scheduled_registry_scope(registry, "read_only"):
            await asyncio.sleep(0)
            return [spec.name for spec in current_scheduled_registry(registry).specs()]

    async def interactive():
        await asyncio.sleep(0)
        return [spec.name for spec in current_scheduled_registry(registry).specs()]

    assert await asyncio.gather(scheduled(), interactive()) == [["read"], ["read", "write", "unknown"]]
