from types import SimpleNamespace

import pytest

from feishu.agent.context import ToolContext
from feishu.agent.replies import ScheduledTurn


@pytest.fixture
def scheduled_turn():
    return ScheduledTurn("chat", {"user_id": "same"}, "scheduled:task:run", "run", "read_only", "tenant-a")


@pytest.mark.parametrize("scheduled", [True, False])
async def test_unscoped_provider_is_blocked(scheduled_turn, scheduled):
    class Provider:
        async def as_user(self, user):
            pytest.fail("unscoped token must not be read")

        async def has_scopes(self, user, scopes):
            pytest.fail("unscoped scopes must not be read")

    event = scheduled_turn if scheduled else SimpleNamespace(tenant_key="tenant-a")
    context = ToolContext(event=event, user=scheduled_turn.user, user_tokens=Provider())
    assert await context.as_user() is None
    assert not await context.has_user_auth(("scope",))


async def test_scoped_provider_uses_tenant(scheduled_turn):
    class Provider:
        async def as_user_for_tenant(self, tenant, user):
            return (tenant, user["user_id"])

        async def has_scopes_for_tenant(self, tenant, user, scopes):
            return (tenant, user["user_id"], scopes) == ("tenant-a", "same", ("scope",))

    context = ToolContext(event=scheduled_turn, user=scheduled_turn.user, user_tokens=Provider())
    assert await context.as_user() == ("tenant-a", "same")
    assert await context.has_user_auth(("scope",))


async def test_fixed_tenant_for_legacy():
    class Provider:
        async def as_user_for_tenant(self, tenant, user):
            return (tenant, user["user_id"])

    context = ToolContext(
        event=SimpleNamespace(), trusted_tenant_key="tenant-a", user={"user_id": "same"}, user_tokens=Provider()
    )
    assert await context.as_user() == ("tenant-a", "same")
    context.event = SimpleNamespace(tenant_key="tenant-b")
    with pytest.raises(PermissionError):
        await context.as_user()
