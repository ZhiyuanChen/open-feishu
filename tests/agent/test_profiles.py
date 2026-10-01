from __future__ import annotations

from types import SimpleNamespace

from feishu.agent import ProfiledAgent
from feishu.agent.profiles import AgentProfile, ProfileRouter
from feishu.agent.session import PendingApproval


def _event(chat_id: str) -> SimpleNamespace:
    return SimpleNamespace(body={"message": {"chat_id": chat_id}})


class _PendingStore:
    def __init__(self, item):
        self.item = item

    async def get(self, _key):
        return self.item


class _Engine:
    def __init__(self, approval=None):
        self.approvals = _PendingStore(approval)
        self.authorizations = _PendingStore(None)
        self.events = []

    async def run(self, event):
        self.events.append(("run", event))

    async def handle_card_action(self, event):
        self.events.append(("card", event))
        return {"profile": "handled"}

    async def _finalize(self, event, text):
        self.events.append(("finalize", event, text))


async def test_profiled_agent_routes_messages_and_card_actions_by_session_namespace() -> None:
    default = _Engine()
    oncall = _Engine(
        PendingApproval(
            approval_id="approval_1",
            session_id="oncall::oc_oncall",
            tool_call_id="call_1",
            tool_name="noop",
            arguments={},
        )
    )
    router = ProfileRouter(
        profiles=(AgentProfile("default"), AgentProfile("oncall", chat_ids=("oc_oncall",))),
        default_profile="default",
    )
    agent = ProfiledAgent({"default": default, "oncall": oncall}, router)

    await agent.run(_event("oc_oncall"))
    result = await agent.handle_card_action(SimpleNamespace(body={"action": {"value": {"__approval__": "approval_1"}}}))

    assert len(oncall.events) == 2
    assert default.events == []
    assert result == {"profile": "handled"}


async def test_profiled_agent_routes_framework_finalization_by_chat() -> None:
    default = _Engine()
    oncall = _Engine()
    router = ProfileRouter(
        profiles=(AgentProfile("default"), AgentProfile("oncall", chat_ids=("oc_oncall",))),
        default_profile="default",
    )
    agent = ProfiledAgent({"default": default, "oncall": oncall}, router)

    await agent._finalize(_event("oc_oncall"), "not allowed")

    assert oncall.events == [("finalize", _event("oc_oncall"), "not allowed")]
    assert default.events == []
