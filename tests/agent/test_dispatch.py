import json
from types import SimpleNamespace

import pytest

from feishu.agent.dispatch import register_agent
from feishu.agent.loop import Agent
from feishu.agent.session import InMemoryPendingApprovalStore, InMemorySessionStore
from feishu.agent.tools import ToolRegistry
from tests._fakes import FakeLlmBackend, text_turn


class FakeDispatcher:
    """Mimics Plan A EventDispatcher .on()/dispatch() contract."""

    def __init__(self):
        self._handlers = {}

    def on(self, event_type):
        def deco(fn):
            self._handlers[event_type] = fn
            return fn

        return deco

    async def dispatch(self, event):
        handler = self._handlers.get(event.event_type)
        if handler is None:
            return None
        return await handler(event)


class RecordingClient:
    def __init__(self):
        self.replies = []
        outer = self

        class _IM:
            async def reply(self, message_id, content, *, msg_type="text", reply_in_thread=None, uuid=None):
                outer.replies.append((message_id, content))
                return {"message_id": "r"}

        self.im = _IM()


def make_agent(client, turns=()):
    return Agent(
        backend=FakeLlmBackend(list(turns)),
        registry=ToolRegistry(),
        store=InMemorySessionStore(),
        client=client,
        approvals=InMemoryPendingApprovalStore(),
    )


def text_event(event_type="im.message.receive_v1"):
    body = {
        "message": {
            "chat_id": "oc_1",
            "message_id": "om_in",
            "message_type": "text",
            "content": json.dumps({"text": "hi"}),
        }
    }
    return SimpleNamespace(event_type=event_type, body=body)


def card_event(event_type="card.action.trigger"):
    return SimpleNamespace(event_type=event_type, body={"action": {"value": {}}, "message": {"chat_id": "oc_1"}})


class TestRegisterAgent:
    @pytest.fixture
    def client(self):
        return RecordingClient()

    async def test_message_routes_to_run(self, client):
        agent = make_agent(client, [text_turn("hello")])
        dispatcher = FakeDispatcher()
        register_agent(dispatcher, agent)

        result = await dispatcher.dispatch(text_event())

        assert result is None  # run() returns None
        assert client.replies == [("om_in", "hello")]

    async def test_card_routes_to_handler(self, client):
        agent = make_agent(client)
        dispatcher = FakeDispatcher()
        register_agent(dispatcher, agent)

        result = await dispatcher.dispatch(card_event())

        assert result == {"toast": {"type": "info", "content": "no pending approval"}}

    async def test_honors_custom_event_types(self, client):
        agent = make_agent(client, [text_turn("hello")])
        dispatcher = FakeDispatcher()
        register_agent(dispatcher, agent, message_event="msg.custom", card_event="card.custom")

        assert await dispatcher.dispatch(text_event("msg.custom")) is None
        assert client.replies == [("om_in", "hello")]
        assert await dispatcher.dispatch(card_event("card.custom")) == {
            "toast": {"type": "info", "content": "no pending approval"}
        }
