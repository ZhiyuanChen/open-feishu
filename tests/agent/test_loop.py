# OpenFeishu
# Copyright (C) 2024-Present  DanLing

# This file is part of OpenFeishu.

# OpenFeishu is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# OpenFeishu is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# For additional terms and clarifications, please refer to our License FAQ at:
# <https://multimolecule.danling.org/about/license-faq>.

"""Agent loop tests.

Merged from the flat suite: loop (run/reply/persist/tool-dispatch/stream),
accumulate_stream, user_message_from_event / session_id_for parsing, card-action
approval flow, and adapter-driven loop integration + cross-adapter parity.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from feishu.agent.adapters.anthropic import AnthropicBackend
from feishu.agent.adapters.openai import OpenAIBackend
from feishu.agent.approval import ApprovalStatus, DefaultApprovalEngine
from feishu.agent.context import ToolContext
from feishu.agent.llm import (
    Message,
    MessageStop,
    ReasoningDelta,
    StopReason,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallDelta,
    ToolResultPart,
    ToolUsePart,
)
from feishu.agent.loop import AgentEngine as Agent
from feishu.agent.loop import StreamResult, accumulate_stream, session_id_for, user_message_from_event
from feishu.agent.persistence import SqlitePendingAuthorizationStore
from feishu.agent.result import ToolOutcome, ToolResult
from feishu.agent.session import (
    ClaimResult,
    InMemoryPendingApprovalStore,
    InMemoryPendingAuthorizationStore,
    InMemorySessionStore,
    PendingApproval,
    PendingAuthorization,
)
from feishu.agent.tools import ToolRegistry
from tests._fakes import FakeLlmBackend, text_turn, tool_turn

SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
DEPLOY_SCHEMA = {"type": "object", "properties": {"env": {"type": "string"}}, "required": ["env"]}


def _ns(**kw):
    return SimpleNamespace(**kw)


def _text_event(text="hi", *, message_id="om_in", chat_id="oc_1", open_id="ou_tester"):
    body = {
        "sender": {"sender_id": {"open_id": open_id}},
        "message": {
            "chat_id": chat_id,
            "message_id": message_id,
            "message_type": "text",
            "content": json.dumps({"text": text}),
        },
    }
    return SimpleNamespace(event_type="im.message.receive_v1", body=body)


async def _agen(items):
    for i in items:
        yield i


# ===========================================================================
# Agent loop: plain replies, tool dispatch, iteration bounds, streaming
# ===========================================================================


class _LoopRecordingClient:
    """Minimal stand-in for FeishuClient exposing reply/card surfaces used by the loop."""

    def __init__(self):
        self.replies = []
        self.sent_cards = []
        self.patched_cards = []
        self.recalled_messages = []
        self.stream_card_calls = []

        class _IM:
            async def reply(_self, message_id, content, *, msg_type="text", reply_in_thread=None, uuid=None):
                self.replies.append((message_id, content, msg_type))
                return {"message_id": "om_reply"}

            async def send(_self, receive_id, content, *, msg_type="interactive", receive_id_type="chat_id", **_kw):
                message_id = f"om_card_{len(self.sent_cards) + 1}"
                self.sent_cards.append((receive_id, content, msg_type, receive_id_type))
                return {"message_id": message_id}

            async def patch(_self, message_id, content, **_kw):
                self.patched_cards.append((message_id, content))
                return {"message_id": message_id}

            async def recall(_self, message_id):
                self.recalled_messages.append(message_id)
                return {}

        self.im = _IM()

    async def stream_card(
        self, tokens, *, receive_id=None, receive_id_type="open_id", reply_to_message_id=None, **kwargs
    ):
        collected = []
        async for token in tokens:
            collected.append(token)
        self.stream_card_calls.append(
            {
                "receive_id": receive_id,
                "receive_id_type": receive_id_type,
                "reply_to_message_id": reply_to_message_id,
                "text": "".join(collected),
                **kwargs,
            }
        )


class _NestedMessageIdClient(_LoopRecordingClient):
    """Recording client whose send response mirrors nested Feishu envelopes."""

    def __init__(self):
        super().__init__()

        class _IM:
            async def reply(_self, message_id, content, *, msg_type="text", reply_in_thread=None, uuid=None):
                self.replies.append((message_id, content, msg_type))
                return {"data": {"message_id": "om_reply"}}

            async def send(_self, receive_id, content, *, msg_type="interactive", receive_id_type="chat_id", **_kw):
                message_id = f"om_card_{len(self.sent_cards) + 1}"
                self.sent_cards.append((receive_id, content, msg_type, receive_id_type))
                return {"data": {"message_id": message_id}}

            async def patch(_self, message_id, content, **_kw):
                self.patched_cards.append((message_id, content))
                return {"data": {"message_id": message_id}}

        self.im = _IM()


class _DenyingScopeProvider:
    def __init__(self):
        self.checked = []

    async def has_scopes(self, user, scopes):
        self.checked.append((dict(user), tuple(scopes)))
        return False

    async def as_user(self, user):
        raise AssertionError("preflight should not fall through to tool execution")


class TestAgentLoop:
    async def test_replies_and_persists(self):
        client = _LoopRecordingClient()
        store = InMemorySessionStore()
        agent = Agent(
            backend=FakeLlmBackend([text_turn("hello there")]), registry=ToolRegistry(), store=store, client=client
        )
        await agent.run(_text_event("hi", message_id="om_in", chat_id="oc_1"))
        assert client.replies == [("om_in", "hello there", "text")]
        history = await store.get("oc_1")
        # user message + assistant message persisted
        assert history[0].role == "user"
        assert history[-1].role == "assistant"

    async def test_system_callback_receives_resolved_timezone(self):
        backend = FakeLlmBackend([text_turn("hello there")])
        agent = Agent(
            backend=backend,
            registry=ToolRegistry(),
            store=InMemorySessionStore(),
            client=_LoopRecordingClient(),
            system=lambda _event, timezone: f"timezone={timezone}",
            timezone=lambda _event: "Europe/Berlin",
        )

        await agent.run(_text_event("hi", message_id="om_in", chat_id="oc_1"))

        assert backend.calls[0]["system"] == "timezone=Europe/Berlin"

    async def test_progress_card_starts_for_plain_reply_and_finalizes_in_place(self):
        client = _LoopRecordingClient()
        store = InMemorySessionStore()

        def builder(tool_names, done, result):
            return {"tools": tool_names, "done": done, "result": result}

        agent = Agent(
            backend=FakeLlmBackend([text_turn("hello there")]),
            registry=ToolRegistry(),
            store=store,
            client=client,
            progress_card_builder=builder,
        )
        await agent.run(_text_event("hi", message_id="om_in", chat_id="oc_1"))
        assert client.sent_cards == [("oc_1", {"tools": [], "done": False, "result": ""}, "interactive", "chat_id")]
        assert client.patched_cards == [("om_card_1", {"tools": [], "done": True, "result": "hello there"})]
        assert client.replies == []

    async def test_progress_card_uses_nested_message_id_for_in_place_updates(self):
        client = _NestedMessageIdClient()
        store = InMemorySessionStore()

        def builder(tool_names, done, result):
            return {"tools": tool_names, "done": done, "result": result}

        agent = Agent(
            backend=FakeLlmBackend([text_turn("hello there")]),
            registry=ToolRegistry(),
            store=store,
            client=client,
            progress_card_builder=builder,
        )
        await agent.run(_text_event("hi", message_id="om_in", chat_id="oc_1"))
        assert len(client.sent_cards) == 1
        assert client.patched_cards == [("om_card_1", {"tools": [], "done": True, "result": "hello there"})]
        assert client.replies == []

    async def test_progress_summarizer_patches_reasoning_status(self):
        client = _LoopRecordingClient()
        store = InMemorySessionStore()
        snapshots = []

        def builder(tool_names, done, result):
            return {"tools": tool_names, "done": done, "result": result}

        async def summarizer(snapshot):
            snapshots.append(snapshot)
            return "正在检查授权并整理日程…"

        agent = Agent(
            backend=FakeLlmBackend(
                [
                    [
                        ReasoningDelta("checking calendar authorization"),
                        TextDelta("done"),
                        MessageStop(stop_reason=StopReason.END_TURN),
                    ]
                ]
            ),
            registry=ToolRegistry(),
            store=store,
            client=client,
            progress_card_builder=builder,
            progress_summarizer=summarizer,
            progress_summary_delay_seconds=0,
            progress_summary_interval_seconds=0,
        )

        await agent.run(_text_event("hi", message_id="om_in", chat_id="oc_1"))

        assert snapshots and snapshots[0].reasoning == "checking calendar authorization"
        assert ("om_card_1", {"tools": [], "done": False, "result": "正在检查授权并整理日程…"}) in client.patched_cards
        assert client.patched_cards[-1] == ("om_card_1", {"tools": [], "done": True, "result": "done"})

    async def test_progress_summarizer_patches_tool_status_from_description(self):
        client = _LoopRecordingClient()
        store = InMemorySessionStore()
        reg = ToolRegistry()
        snapshots = []

        async def weather(city):
            await asyncio.sleep(0.02)
            return f"sunny in {city}"

        reg.register("weather", weather, input_schema=SCHEMA, description="查询指定城市的天气。")

        def builder(tool_names, done, result):
            return {"tools": tool_names, "done": done, "result": result}

        async def summarizer(snapshot):
            snapshots.append(snapshot)
            if snapshot.phase == "tool":
                return "正在查询天气…"
            return None

        agent = Agent(
            backend=FakeLlmBackend(
                [
                    tool_turn(index=0, id="c1", name="weather", arguments_json='{"city":"sh"}'),
                    text_turn("done"),
                ]
            ),
            registry=reg,
            store=store,
            client=client,
            progress_card_builder=builder,
            progress_summarizer=summarizer,
        )

        await agent.run(_text_event("weather", message_id="om_in", chat_id="oc_1"))

        tool_snapshots = [snapshot for snapshot in snapshots if snapshot.phase == "tool"]
        assert tool_snapshots
        assert tool_snapshots[0].tool_name == "weather"
        assert tool_snapshots[0].tool_description == "查询指定城市的天气。"
        assert ("om_card_1", {"tools": ["weather"], "done": False, "result": "正在查询天气…"}) in client.patched_cards

    async def test_new_message_interrupts_active_model_turn_for_same_session(self):
        class BlockingFirstTurnBackend:
            def __init__(self):
                self.calls = []
                self.first_started = asyncio.Event()
                self.first_cancelled = asyncio.Event()
                self._never = asyncio.Event()

            def stream(self, *, messages, tools=(), system=None, **kwargs):
                self.calls.append(
                    {"messages": list(messages), "tools": list(tools), "system": system, "kwargs": kwargs}
                )

                async def first():
                    self.first_started.set()
                    try:
                        await self._never.wait()
                    except asyncio.CancelledError:
                        self.first_cancelled.set()
                        raise
                    yield MessageStop(stop_reason=StopReason.END_TURN)

                if len(self.calls) == 1:
                    return first()
                return _agen(text_turn("new answer"))

        client = _LoopRecordingClient()
        store = InMemorySessionStore()
        backend = BlockingFirstTurnBackend()

        def builder(tool_names, done, result):
            return {"tools": tool_names, "done": done, "result": result}

        agent = Agent(
            backend=backend,
            registry=ToolRegistry(),
            store=store,
            client=client,
            progress_card_builder=builder,
            interrupted_progress_text="Interrupted by a newer message.",
        )

        first = asyncio.create_task(agent.run(_text_event("old", message_id="om_old", chat_id="oc_1")))
        await asyncio.wait_for(backend.first_started.wait(), timeout=1)
        second = asyncio.create_task(agent.run(_text_event("new", message_id="om_new", chat_id="oc_1")))

        await asyncio.wait_for(second, timeout=1)
        await asyncio.wait_for(backend.first_cancelled.wait(), timeout=1)
        await asyncio.wait_for(first, timeout=1)

        patched = dict(client.patched_cards)
        assert patched["om_card_1"] == {"tools": [], "done": True, "result": "Interrupted by a newer message."}
        assert patched["om_card_2"] == {"tools": [], "done": True, "result": "new answer"}

    async def test_new_message_interrupts_active_tool_and_records_interrupted_result(self):
        client = _LoopRecordingClient()
        store = InMemorySessionStore()
        reg = ToolRegistry()
        tool_started = asyncio.Event()
        tool_cancelled = asyncio.Event()
        never = asyncio.Event()

        async def slow_tool():
            tool_started.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                tool_cancelled.set()
                raise

        reg.register("slow_tool", slow_tool, input_schema={"type": "object", "properties": {}}, description="slow")

        def builder(tool_names, done, result):
            return {"tools": tool_names, "done": done, "result": result}

        agent = Agent(
            backend=FakeLlmBackend(
                [
                    tool_turn(index=0, id="c1", name="slow_tool", arguments_json="{}"),
                    text_turn("new answer"),
                ]
            ),
            registry=reg,
            store=store,
            client=client,
            progress_card_builder=builder,
            interrupted_progress_text="Interrupted by a newer message.",
        )

        first = asyncio.create_task(agent.run(_text_event("old", message_id="om_old", chat_id="oc_1")))
        await asyncio.wait_for(tool_started.wait(), timeout=1)
        second = asyncio.create_task(agent.run(_text_event("new", message_id="om_new", chat_id="oc_1")))

        await asyncio.wait_for(second, timeout=1)
        await asyncio.wait_for(tool_cancelled.wait(), timeout=1)
        await asyncio.wait_for(first, timeout=1)

        history = await store.get("oc_1")
        interrupted_results = [
            part
            for msg in history
            if msg.role == "tool"
            for part in msg.content
            if isinstance(part, ToolResultPart) and part.tool_call_id == "c1"
        ]
        assert len(interrupted_results) == 1
        assert interrupted_results[0].is_error is True
        assert "Interrupted by a newer user message" in str(interrupted_results[0].content)
        patched = dict(client.patched_cards)
        assert patched["om_card_1"] == {
            "tools": ["slow_tool"],
            "done": True,
            "result": "Interrupted by a newer message.",
        }
        assert patched["om_card_2"] == {"tools": [], "done": True, "result": "new answer"}

    async def test_dispatches_and_reinvokes(self):
        client = _LoopRecordingClient()
        reg = ToolRegistry()
        calls = []

        async def weather(city):
            calls.append(city)
            return f"sunny in {city}"

        reg.register("weather", weather, input_schema=SCHEMA, description="d")
        # Turn 1: ask to use weather; Turn 2: final text
        backend = FakeLlmBackend(
            [
                tool_turn(index=0, id="c1", name="weather", arguments_json='{"city":"sh"}'),
                text_turn("It is sunny in sh"),
            ]
        )
        agent = Agent(backend=backend, registry=reg, store=InMemorySessionStore(), client=client)
        await agent.run(_text_event("weather?"))
        assert calls == ["sh"]
        assert client.replies[-1][1] == "It is sunny in sh"
        # second backend call must include the tool result in messages
        second_msgs = backend.calls[1]["messages"]
        assert any(m.role == "tool" for m in second_msgs)

    async def test_carries_tool_call_id(self):
        client = _LoopRecordingClient()
        reg = ToolRegistry()

        async def weather(city):
            return "sunny"

        reg.register("weather", weather, input_schema=SCHEMA, description="d")
        backend = FakeLlmBackend(
            [
                tool_turn(index=0, id="call_42", name="weather", arguments_json='{"city":"sh"}'),
                text_turn("done"),
            ]
        )
        store = InMemorySessionStore()
        agent = Agent(backend=backend, registry=reg, store=store, client=client)
        await agent.run(_text_event("q", chat_id="oc_z"))
        history = await store.get("oc_z")
        tool_msgs = [m for m in history if m.role == "tool"]
        assert tool_msgs and tool_msgs[0].content[0].tool_call_id == "call_42"

    async def test_authorization_resume_replays_original_tool_call_and_finalizes(self):
        client = _LoopRecordingClient()
        reg = ToolRegistry()
        store = InMemorySessionStore()
        authorizations = InMemoryPendingAuthorizationStore()
        calls = []

        async def events():
            calls.append("events")
            if len(calls) == 1:
                return ToolResult(
                    ToolOutcome.NEEDS_USER_AUTH,
                    content="user authorization required",
                    auth_scopes=("calendar:calendar",),
                    is_error=True,
                )
            return "events result"

        reg.register("events", events, input_schema={"type": "object"}, description="events")
        backend = FakeLlmBackend(
            [
                tool_turn(index=0, id="c1", name="events", arguments_json="{}"),
                text_turn("Here are your events"),
            ]
        )
        seen_authorizations = []

        def authorize_url_builder(user, scopes, authorization=None):
            seen_authorizations.append((dict(user), tuple(scopes), authorization.authorization_id))
            return f"https://auth.example/authorize?state={authorization.authorization_id}"

        agent = Agent(
            backend=backend,
            registry=reg,
            store=store,
            client=client,
            authorizations=authorizations,
            auth_card_builder=lambda url: {"url": url},
            authorize_url_builder=authorize_url_builder,
        )

        await agent.run(_text_event("calendar?", chat_id="oc_1", open_id="ou_tester"))

        assert calls == ["events"]
        assert len(backend.calls) == 1
        assert len(client.sent_cards) == 1
        pending = next(iter(authorizations._store.values()))
        assert seen_authorizations == [({"open_id": "ou_tester"}, ("calendar:calendar",), pending.authorization_id)]
        assert pending.extra["auth_card_message_id"] == "om_card_1"
        history = await store.get("oc_1")
        results = [p for m in history if m.role == "tool" for p in m.content if isinstance(p, ToolResultPart)]
        assert [p.tool_call_id for p in results] == ["c1"]
        assert "Awaiting user authorization" in results[0].content

        status = await agent.resume_authorization(pending.authorization_id, user={"open_id": "ou_tester"})

        assert status == "resumed"
        assert calls == ["events", "events"]
        assert len(backend.calls) == 2
        assert client.replies[-1][1] == "Here are your events"
        assert client.recalled_messages == ["om_card_1"]
        assert authorizations._store == {}
        history = await store.get("oc_1")
        results = [p for m in history if m.role == "tool" for p in m.content if isinstance(p, ToolResultPart)]
        assert len(results) == 1
        assert results[0].content == "events result"

    async def test_authorization_card_message_id_is_persisted_for_sqlite_store(self, tmp_path):
        client = _LoopRecordingClient()
        reg = ToolRegistry()
        store = InMemorySessionStore()
        authorizations = SqlitePendingAuthorizationStore(tmp_path / "auth.db")
        calls = []

        async def events():
            calls.append("events")
            if len(calls) > 1:
                return "sent"
            return ToolResult(
                ToolOutcome.NEEDS_USER_AUTH,
                content="user authorization required",
                auth_scopes=("mail:user_mailbox.message:send",),
                is_error=True,
            )

        reg.register("events", events, input_schema={"type": "object"}, description="events")
        backend = FakeLlmBackend(
            [
                tool_turn(index=0, id="c1", name="events", arguments_json="{}"),
                text_turn("sent"),
            ]
        )
        seen_authorizations = []

        def authorize_url_builder(user, scopes, authorization=None):
            seen_authorizations.append(authorization.authorization_id)
            return f"https://auth.example/authorize?state={authorization.authorization_id}"

        agent = Agent(
            backend=backend,
            registry=reg,
            store=store,
            client=client,
            authorizations=authorizations,
            auth_card_builder=lambda url: {"url": url},
            authorize_url_builder=authorize_url_builder,
        )

        await agent.run(_text_event("send mail", chat_id="oc_1", open_id="ou_tester"))

        pending = await authorizations.get(seen_authorizations[0])
        assert pending is not None
        assert pending.extra["auth_card_message_id"] == "om_card_1"

        status = await agent.resume_authorization(pending.authorization_id, user={"open_id": "ou_tester"})

        assert status == "resumed"
        assert calls == ["events", "events"]
        assert client.recalled_messages == ["om_card_1"]

    async def test_auth_preflight_runs_before_approval_card(self):
        client = _LoopRecordingClient()
        reg = ToolRegistry()
        user_tokens = _DenyingScopeProvider()
        store = InMemorySessionStore()
        authorizations = InMemoryPendingAuthorizationStore()
        calls = []

        async def create_event():
            calls.append("create_event")
            return "created"

        reg.register(
            "create_event",
            create_event,
            input_schema={"type": "object"},
            description="create event",
            requires_approval=True,
            auth_scopes=("calendar:calendar",),
        )
        backend = FakeLlmBackend([tool_turn(index=0, id="c1", name="create_event", arguments_json="{}")])
        seen_authorizations = []

        def authorize_url_builder(user, scopes, authorization=None):
            seen_authorizations.append((dict(user), tuple(scopes), authorization.authorization_id))
            return f"https://auth.example/authorize?state={authorization.authorization_id}"

        agent = Agent(
            backend=backend,
            registry=reg,
            store=store,
            client=client,
            authorizations=authorizations,
            approval_card_builder=lambda _approval: {"approval": True},
            auth_card_builder=lambda url: {"auth": url},
            authorize_url_builder=authorize_url_builder,
            user_tokens=user_tokens,
        )

        await agent.run(_text_event("calendar?", chat_id="oc_1", open_id="ou_tester"))

        assert calls == []
        assert user_tokens.checked == [({"open_id": "ou_tester"}, ("calendar:calendar",))]
        assert len(client.sent_cards) == 1
        assert "auth" in client.sent_cards[0][1]
        assert "approval" not in client.sent_cards[0][1]
        pending = next(iter(authorizations._store.values()))
        assert seen_authorizations == [({"open_id": "ou_tester"}, ("calendar:calendar",), pending.authorization_id)]

    async def test_authorization_resume_requires_callback_user(self):
        client = _LoopRecordingClient()
        reg = ToolRegistry()
        store = InMemorySessionStore()
        authorizations = InMemoryPendingAuthorizationStore()
        calls = []

        async def events():
            calls.append("events")
            return ToolResult(ToolOutcome.NEEDS_USER_AUTH, content="auth", auth_scopes=("calendar:calendar",))

        reg.register("events", events, input_schema={"type": "object"}, description="events")
        backend = FakeLlmBackend([tool_turn(index=0, id="c1", name="events", arguments_json="{}")])
        agent = Agent(
            backend=backend,
            registry=reg,
            store=store,
            client=client,
            authorizations=authorizations,
            auth_card_builder=lambda url: {"url": url},
            authorize_url_builder=lambda user, scopes, authorization: "https://auth.example",
        )
        await agent.run(_text_event("calendar?", chat_id="oc_1", open_id="ou_tester"))
        pending = next(iter(authorizations._store.values()))

        status = await agent.resume_authorization(pending.authorization_id)

        assert status == "forbidden"
        assert calls == ["events"]
        assert client.replies[-1][1].startswith("授权已完成，但无法确认完成授权的用户身份")
        assert pending.authorization_id in authorizations._store

    async def test_authorization_resume_expired_pending_reports_to_chat(self, tmp_path):
        client = _LoopRecordingClient()
        authorizations = SqlitePendingAuthorizationStore(tmp_path / "auth.db", ttl_seconds=1)
        pending = PendingAuthorization(
            authorization_id="az_expired",
            session_id="oc_1",
            tool_call_id="c1",
            tool_name="events",
            arguments={},
            owner_user_keys=("open_id:ou_tester",),
            chat_id="oc_1",
            created_message_id="om_in",
            created_at=0,
        )
        await authorizations.put(pending)
        agent = Agent(
            backend=FakeLlmBackend([]),
            registry=ToolRegistry(),
            store=InMemorySessionStore(),
            client=client,
            authorizations=authorizations,
        )

        status = await agent.resume_authorization("az_expired", user={"open_id": "ou_tester"})

        assert status == "expired"
        assert client.replies[-1][0] == "om_in"
        assert "原请求已过期" in client.replies[-1][1]

    def test_authorize_url_builder_type_error_is_not_treated_as_legacy_signature(self):
        def builder(_user, _scopes, _authorization):
            raise TypeError("inner builder bug")

        agent = Agent(
            backend=FakeLlmBackend([]),
            registry=ToolRegistry(),
            store=InMemorySessionStore(),
            authorize_url_builder=builder,
        )
        pending = PendingAuthorization(
            authorization_id="az_1",
            session_id="s",
            tool_call_id="c1",
            tool_name="events",
            arguments={},
        )

        with pytest.raises(TypeError, match="inner builder bug"):
            agent._build_authorize_url({"open_id": "ou_1"}, (), pending)

    def test_legacy_two_argument_authorize_url_builder_still_works(self):
        agent = Agent(
            backend=FakeLlmBackend([]),
            registry=ToolRegistry(),
            store=InMemorySessionStore(),
            authorize_url_builder=lambda _user, _scopes: "https://auth.example",
        )
        pending = PendingAuthorization(
            authorization_id="az_1",
            session_id="s",
            tool_call_id="c1",
            tool_name="events",
            arguments={},
        )

        assert agent._build_authorize_url({"open_id": "ou_1"}, (), pending) == "https://auth.example"

    async def test_stream_via_stream_card(self):
        """stream=True routes the final reply through client.stream_card in reply position (in-thread)."""
        client = _LoopRecordingClient()
        backend = FakeLlmBackend([text_turn("streamed reply")])
        agent = Agent(
            backend=backend,
            registry=ToolRegistry(),
            store=InMemorySessionStore(),
            client=client,
            stream=True,
        )
        await agent.run(_text_event("hi", message_id="om_in", chat_id="oc_s"))
        # stream_card replies in-thread to the inbound message — same surface as stream=False.
        assert len(client.stream_card_calls) == 1
        call = client.stream_card_calls[0]
        assert call["reply_to_message_id"] == "om_in"
        assert call["receive_id"] is None
        assert call["text"] == "streamed reply"
        # im.reply must NOT have been called
        assert client.replies == []

    async def test_stream_no_message_id_noop(self):
        """Finalizing a streamed reply with no message_id must not call stream_card (graceful no-op)."""
        client = _LoopRecordingClient()
        backend = FakeLlmBackend([text_turn("streamed reply")])
        agent = Agent(
            backend=backend,
            registry=ToolRegistry(),
            store=InMemorySessionStore(),
            client=client,
            stream=True,
        )
        # chat_id present (so the session resolves) but no message_id -> nothing to reply to.
        body = {"message": {"chat_id": "oc_s", "message_type": "text", "content": '{"text":"hi"}'}}
        event = SimpleNamespace(event_type="im.message.receive_v1", body=body)
        await agent.run(event)
        # no message to reply to -> graceful no-op
        assert client.stream_card_calls == []

    async def test_max_iterations_fallback(self):
        """max_iterations bounds an otherwise-infinite tool loop, and a fallback reply is still sent."""
        client = _LoopRecordingClient()
        reg = ToolRegistry()

        async def loop_tool():
            return "again"

        reg.register("loop_tool", loop_tool, input_schema={"type": "object", "properties": {}}, description="d")
        backend = FakeLlmBackend(
            [tool_turn(index=0, id="c", name="loop_tool", arguments_json="{}")],
            repeat_last=True,
        )
        agent = Agent(backend=backend, registry=reg, store=InMemorySessionStore(), client=client, max_iterations=3)
        await agent.run(_text_event("go"))
        # exactly max_iterations backend invocations
        assert len(backend.calls) == 3
        # a non-empty fallback reply must have been sent — the user is not left with silence
        assert len(client.replies) == 1
        _msg_id, reply_text, _msg_type = client.replies[0]
        assert reply_text


# ===========================================================================
# accumulate_stream: text/tool reassembly, usage capture
# ===========================================================================


class TestAccumulateStream:
    async def test_text_and_stop_reason(self):
        result = await accumulate_stream(
            _agen([TextDelta(text="he"), TextDelta(text="llo"), MessageStop(stop_reason=StopReason.END_TURN)])
        )
        assert isinstance(result, StreamResult)
        assert result.text == "hello"
        assert result.tool_calls == []
        assert result.stop_reason == StopReason.END_TURN

    async def test_fragmented_tool_call(self):
        # A tool call whose arguments JSON is split across deltas must reassemble into a
        # single ToolCall with valid, dispatchable arguments.
        chunks = [
            ToolCallDelta(index=0, id="c1", name="weather", arguments='{"ci'),
            ToolCallDelta(index=0, arguments='ty":"sh"}'),
            MessageStop(stop_reason=StopReason.TOOL_USE),
        ]
        result = await accumulate_stream(_agen(chunks))
        assert result.tool_calls == [ToolCall(id="c1", name="weather", arguments='{"city":"sh"}')]
        assert result.stop_reason == StopReason.TOOL_USE

    async def test_concurrent_tool_calls(self):
        # Two interleaved tool calls (distinct indices) each produce a complete, correct call.
        chunks = [
            ToolCallDelta(index=1, id="c2", name="b", arguments='{"y'),
            ToolCallDelta(index=0, id="c1", name="a", arguments='{"x'),
            ToolCallDelta(index=1, arguments='":2}'),
            ToolCallDelta(index=0, arguments='":1}'),
            MessageStop(stop_reason=StopReason.TOOL_USE),
        ]
        result = await accumulate_stream(_agen(chunks))
        by_id = {tc.id: tc for tc in result.tool_calls}
        assert by_id["c1"] == ToolCall(id="c1", name="a", arguments='{"x":1}')
        assert by_id["c2"] == ToolCall(id="c2", name="b", arguments='{"y":2}')

    async def test_usage_captured(self):
        result = await accumulate_stream(
            _agen([MessageStop(stop_reason=StopReason.END_TURN, usage={"input_tokens": 5})])
        )
        assert result.usage == {"input_tokens": 5}


# ===========================================================================
# Event parsing: session_id_for / user_message_from_event
# ===========================================================================


class TestEventParsing:
    @staticmethod
    def _event(body):
        return SimpleNamespace(event_type="im.message.receive_v1", body=body)

    def test_session_id_uses_chat_id(self):
        ev = self._event({"message": {"chat_id": "oc_abc", "message_id": "om_1"}})
        assert session_id_for(ev) == "oc_abc"

    def test_session_id_thread_reply(self):
        ev = self._event({"message": {"chat_id": "oc_abc", "message_id": "om_2", "root_id": "om_root"}})
        assert session_id_for(ev) == "oc_abc:om_root"

    def test_session_id_without_chat(self):
        ev = self._event({"message": {"message_id": "om_9"}})
        assert session_id_for(ev) == "om_9"

    def test_parses_text(self):
        ev = self._event(
            {"message": {"chat_id": "oc", "message_type": "text", "content": json.dumps({"text": "hello bot"})}}
        )
        msg = user_message_from_event(ev)
        assert msg == Message(role="user", content=[TextPart(text="hello bot")])

    def test_strips_mention(self):
        ev = self._event(
            {"message": {"message_type": "text", "content": json.dumps({"text": "@_user_1 what is the weather"})}}
        )
        msg = user_message_from_event(ev)
        assert msg.content[0].text == "what is the weather"

    def test_non_text_uses_neutral_placeholder(self):
        raw = json.dumps({"image_key": "img_x"})
        ev = self._event({"message": {"message_type": "image", "content": raw}})
        msg = user_message_from_event(ev)
        assert msg.content[0].text == "[image message]"

    def test_invalid_json(self):
        ev = self._event({"message": {"message_type": "text", "content": "not-json"}})
        msg = user_message_from_event(ev)
        assert msg == Message(role="user", content=[TextPart(text="not-json")])

    def test_no_message_raises(self):
        ev = self._event({})
        with pytest.raises(ValueError):
            user_message_from_event(ev)


# ===========================================================================
# Approval flow: requires_approval card suspend/resume via handle_card_action
# ===========================================================================


class _ApprovalRecordingClient:
    def __init__(self):
        self.replies = []
        self.cards = []
        self.patches = []
        self.recalls = []
        outer = self

        class _IM:
            async def reply(self, message_id, content, *, msg_type="text", reply_in_thread=None, uuid=None):
                outer.replies.append((message_id, content, msg_type))
                return {"message_id": "om_reply"}

            async def send(self, receive_id, content, *, msg_type=None, receive_id_type=None, uuid=None):
                outer.cards.append((content, receive_id))
                return {"message_id": "om_card"}

            async def patch(self, message_id, content):
                outer.patches.append((message_id, content))
                return {"message_id": message_id}

            async def recall(self, message_id):
                outer.recalls.append(message_id)
                return {}

        self.im = _IM()


def _action_event(approval_id, decision, chat_id="oc_1", *, open_id="ou_tester", payload_sha256=None):
    # A realistic card.action.trigger: the clicker is in `operator`, the card's message is in `context`
    # (there is NO message{} node — the resumed loop must recover the conversation from the stored approval).
    # A real card button also carries payload_sha256 for the tamper check; mirror that when supplied.
    value = {"__approval__": approval_id, "decision": decision}
    if payload_sha256 is not None:
        value["payload_sha256"] = payload_sha256
    body = {
        "action": {"value": value},
        "operator": {"open_id": open_id},
        "context": {"open_message_id": "om_card", "open_chat_id": chat_id},
    }
    return SimpleNamespace(event_type="card.action.trigger", body=body)


async def test_tool_context_prefers_card_callback_timezone():
    event = _action_event("ap_1", "approve")
    event.body["context"]["timezone"] = "America/New_York"

    timezone = await ToolContext(event=event, timezone="Asia/Shanghai").current_timezone("Asia/Shanghai")

    assert timezone == "America/New_York"


async def _drain(agent) -> None:
    r"""Await the background tasks handle_card_action spawns (decide → execute → resume → patch)."""
    while agent._bg_tasks:
        await asyncio.gather(*list(agent._bg_tasks))


class _MemoryExecutionStore:
    def __init__(self):
        self.rows = {}

    def get(self, lookup_key):
        return self.rows.get(lookup_key)

    def put(
        self,
        idempotency_key,
        *,
        execution_status,
        result,
        alias_lookup_keys=(),
        payload_sha256=None,
    ):
        row = {
            "execution_status": execution_status,
            "result": result,
            "payload_sha256": payload_sha256,
        }
        self.rows[idempotency_key] = row
        for key in alias_lookup_keys:
            self.rows[key] = row


def _agent_with_deploy(client, store, approvals, ran, *, progress_card_builder=None, system=None, timezone=None):
    reg = ToolRegistry()

    async def deploy(env):
        ran.append(env)
        return f"deployed to {env}"

    reg.register("deploy", deploy, input_schema=DEPLOY_SCHEMA, description="deploy", requires_approval=True)
    backend = FakeLlmBackend(
        [
            tool_turn(index=0, id="c1", name="deploy", arguments_json='{"env":"prod"}'),
            text_turn("deployment complete"),
        ]
    )
    return (
        Agent(
            backend=backend,
            registry=reg,
            store=store,
            client=client,
            approvals=approvals,
            progress_card_builder=progress_card_builder,
            system=system,
            timezone=timezone,
        ),
        backend,
    )


def _extract_approval_id(card: dict) -> str:
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "__approval__" in node:
                found.append(node["__approval__"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(card)
    assert found, f"no __approval__ value embedded in card: {card}"
    return found[0]


def _extract_payload_sha256(card: dict) -> str | None:
    """Pull the payload_sha256 a real approval card embeds in its button value (for the tamper check)."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "payload_sha256" in node:
                found.append(node["payload_sha256"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(card)
    return found[0] if found else None


def _has_buttons(card: dict) -> bool:
    flag = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("tag") == "button":
                flag.append(True)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(card)
    return bool(flag)


class TestApprovalFlow:
    async def test_sends_card_and_suspends(self):
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []
        agent, backend = _agent_with_deploy(client, store, approvals, ran)
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        # tool NOT run yet; an interactive card was sent; only one backend call so far
        assert ran == []
        assert len(client.cards) == 1
        assert len(backend.calls) == 1
        # a PendingApproval was persisted -> there is exactly one to pop
        history = await store.get("oc_1")
        assert any(m.role == "assistant" for m in history)

    async def test_approval_resume_reuses_suspended_progress_card(self):
        client = _LoopRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []

        def builder(tool_names, done, result):
            return {"tools": tool_names, "done": done, "result": result}

        agent, _ = _agent_with_deploy(client, store, approvals, ran, progress_card_builder=builder)

        await agent.run(_text_event("deploy prod", chat_id="oc_1"))

        assert len(client.sent_cards) == 2
        progress_message_id = "om_card_1"
        approval_message_id = "om_card_2"
        approval_card = client.sent_cards[1][1]
        approval_id = _extract_approval_id(approval_card)
        sha = _extract_payload_sha256(approval_card)

        event = _action_event(approval_id, "approve", chat_id="oc_1", payload_sha256=sha)
        event.body["context"]["open_message_id"] = approval_message_id
        await agent.handle_card_action(event)
        await _drain(agent)

        assert ran == ["prod"]
        assert len(client.sent_cards) == 2  # no extra progress card on resume
        patched_ids = [message_id for message_id, _ in client.patched_cards]
        assert progress_message_id in patched_ids
        assert approval_message_id in patched_ids
        assert client.patched_cards[-1][0] == approval_message_id

    async def test_suspended_history_is_well_formed_and_placeholder_is_replaced(self):
        """[self-review] A suspended turn must leave every tool_use with a tool_result placeholder (so abandoning
        the card can't make the next turn send malformed history), and approving REPLACES that placeholder —
        exactly one result for the tool_call_id, never a duplicate."""
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []
        agent, _ = _agent_with_deploy(client, store, approvals, ran)
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        history = await store.get("oc_1")
        use_ids = [p.id for m in history if m.role == "assistant" for p in m.content if isinstance(p, ToolUsePart)]
        results = [p for m in history if m.role == "tool" for p in m.content if isinstance(p, ToolResultPart)]
        assert use_ids == ["c1"]  # the suspended tool call
        assert [p.tool_call_id for p in results] == ["c1"]  # placeholder present -> well-formed
        assert "deployed to prod" not in str(results[0].content)  # placeholder, not an executed result
        # Approve: the placeholder is replaced in place, not appended alongside.
        card, _ = client.cards[0]
        approval_id = _extract_approval_id(card)
        sha = _extract_payload_sha256(card)
        await agent.handle_card_action(_action_event(approval_id, "approve", chat_id="oc_1", payload_sha256=sha))
        await _drain(agent)
        history = await store.get("oc_1")
        results = [
            p
            for m in history
            if m.role == "tool"
            for p in m.content
            if isinstance(p, ToolResultPart) and p.tool_call_id == "c1"
        ]
        assert len(results) == 1  # replaced, not duplicated
        assert "deployed to prod" in str(results[0].content)
        assert ran == ["prod"]

    async def test_replayed_approval_replaces_placeholder_and_resumes(self):
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        executions = _MemoryExecutionStore()
        ran = []
        agent, backend = _agent_with_deploy(client, store, approvals, ran)
        agent.approval_engine = DefaultApprovalEngine(approvals=approvals, executions=executions)

        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        pending = next(iter(approvals._store.values()))
        executions.put(
            pending.idempotency_key,
            execution_status="executed",
            result="cached deployment result",
            payload_sha256=pending.payload_sha256,
        )

        card, _ = client.cards[0]
        approval_id = _extract_approval_id(card)
        sha = _extract_payload_sha256(card)
        await agent.handle_card_action(_action_event(approval_id, "approve", chat_id="oc_1", payload_sha256=sha))
        await _drain(agent)

        assert ran == []
        history = await store.get("oc_1")
        results = [
            part
            for msg in history
            if msg.role == "tool"
            for part in msg.content
            if isinstance(part, ToolResultPart) and part.tool_call_id == "c1"
        ]
        assert len(results) == 1
        assert results[0].content == "cached deployment result"
        assert "Awaiting your confirmation" not in str(results[0].content)
        assert len(backend.calls) == 2
        assert client.replies[-1][1] == "deployment complete"

    async def test_approval_resume_executes_later_tool_calls_from_same_turn(self):
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []
        reg = ToolRegistry()

        async def deploy(env):
            ran.append(("deploy", env))
            return f"deployed to {env}"

        async def weather(city):
            ran.append(("weather", city))
            return f"sunny in {city}"

        reg.register("deploy", deploy, input_schema=DEPLOY_SCHEMA, description="deploy", requires_approval=True)
        reg.register("weather", weather, input_schema=SCHEMA, description="weather")
        backend = FakeLlmBackend(
            [
                [
                    ToolCallDelta(index=0, id="c1", name="deploy", arguments='{"env":"prod"}'),
                    ToolCallDelta(index=1, id="c2", name="weather", arguments='{"city":"sh"}'),
                    MessageStop(stop_reason=StopReason.TOOL_USE),
                ],
                text_turn("all done"),
            ]
        )
        agent = Agent(backend=backend, registry=reg, store=store, client=client, approvals=approvals)
        await agent.run(_text_event("deploy prod then check weather", chat_id="oc_1"))
        assert ran == []

        card, _ = client.cards[0]
        approval_id = _extract_approval_id(card)
        sha = _extract_payload_sha256(card)
        await agent.handle_card_action(_action_event(approval_id, "approve", chat_id="oc_1", payload_sha256=sha))
        await _drain(agent)

        assert ran == [("deploy", "prod"), ("weather", "sh")]
        history = await store.get("oc_1")
        results = {
            part.tool_call_id: part.content
            for msg in history
            if msg.role == "tool"
            for part in msg.content
            if isinstance(part, ToolResultPart)
        }
        assert results["c1"] == "deployed to prod"
        assert results["c2"] == "sunny in sh"
        assert "Awaiting your confirmation" not in results["c2"]
        assert client.replies[-1][1] == "all done"

    async def test_approve_runs_and_finalizes(self):
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []
        agent, backend = _agent_with_deploy(client, store, approvals, ran)
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        # grab the approval_id from the persisted approval via the card value
        card, _ = client.cards[0]
        approval_id = _extract_approval_id(card)
        sha = _extract_payload_sha256(card)
        response = await agent.handle_card_action(
            _action_event(approval_id, "approve", chat_id="oc_1", payload_sha256=sha)
        )
        # Immediate ACK: a processing toast, no card synchronously (the decided card is patched in the background).
        assert "toast" in response and "card" not in response
        assert ran == []  # not yet — execution runs in the background
        await _drain(agent)
        assert ran == ["prod"]  # tool ran on approval
        assert client.replies[-1][1] == "deployment complete"  # loop resumed + finalized
        # the clicked card was patched in place with the decided card (buttons removed)
        assert client.patches and not _has_buttons(client.patches[-1][1])

    async def test_approve_ack_does_not_wait_for_pending_lookup(self):
        class BlockingGetApprovalStore(InMemoryPendingApprovalStore):
            def __init__(self):
                super().__init__()
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def get(self, approval_id: str):
                self.started.set()
                await self.release.wait()
                return await super().get(approval_id)

        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = BlockingGetApprovalStore()
        ran = []
        agent, _ = _agent_with_deploy(client, store, approvals, ran)
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        card, _ = client.cards[0]
        approval_id = _extract_approval_id(card)
        sha = _extract_payload_sha256(card)

        task = asyncio.create_task(
            agent.handle_card_action(
                _action_event(
                    approval_id,
                    "approve",
                    chat_id="oc_1",
                    payload_sha256=sha,
                )
            )
        )
        response = await asyncio.wait_for(task, timeout=0.1)

        assert response == {"toast": {"type": "info", "content": "处理中…"}}
        assert ran == []
        assert approvals.started.is_set()

        approvals.release.set()
        await _drain(agent)
        assert ran == ["prod"]

    async def test_reject_skips_tool(self):
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []
        agent, backend = _agent_with_deploy(client, store, approvals, ran)
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        card, _ = client.cards[0]
        approval_id = _extract_approval_id(card)
        sha = _extract_payload_sha256(card)
        response = await agent.handle_card_action(
            _action_event(approval_id, "reject", chat_id="oc_1", payload_sha256=sha)
        )
        assert "toast" in response  # immediate ACK
        await _drain(agent)
        assert ran == []  # tool did NOT run
        # the model still gets to react -> backend called a second time -> a reply emitted
        assert len(backend.calls) == 2

    async def test_unknown_info_toast(self):
        client = _ApprovalRecordingClient()
        agent, _ = _agent_with_deploy(client, InMemorySessionStore(), InMemoryPendingApprovalStore(), [])
        response = await agent.handle_card_action(_action_event("does-not-exist", "approve"))
        assert response["toast"]["type"] == "info"

    async def test_callback_omitting_hash_does_not_bypass_tamper_check(self):
        """[self-review] A callback that omits payload_sha256 while a hash was stored must NOT bypass the tamper
        check — the claim fails closed (no match) and the approved tool never runs."""
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []
        agent, _ = _agent_with_deploy(client, store, approvals, ran)
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        approval_id = _extract_approval_id(client.cards[0][0])
        # Deliberately omit payload_sha256 (malformed/forged callback) -> must be treated as a mismatch.
        await agent.handle_card_action(_action_event(approval_id, "approve", chat_id="oc_1"))
        await _drain(agent)
        assert ran == []  # fail-closed: the write did not execute

    async def test_default_approval_card_summarizes_arguments(self):
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        reg = ToolRegistry()

        async def reimburse(form, accounts):
            return "ok"

        reg.register(
            "reimburse",
            reimburse,
            input_schema={"type": "object"},
            description="reimburse",
            requires_approval=True,
        )
        backend = FakeLlmBackend(
            [
                tool_turn(
                    index=0,
                    id="c1",
                    name="reimburse",
                    arguments_json=json.dumps(
                        {
                            "form": {"amount": 123, "reason": "travel"},
                            "accounts": {"bank": "pa_secret_handle"},
                        }
                    ),
                )
            ]
        )
        agent = Agent(backend=backend, registry=reg, store=store, client=client, approvals=approvals)

        await agent.run(_text_event("submit reimbursement", chat_id="oc_1"))

        body = json.dumps(client.cards[0][0], ensure_ascii=False)
        assert "pa_secret_handle" not in body
        assert "travel" not in body
        assert "确认执行 reimburse？" in body
        assert "确认执行" in body
        assert "拒绝" in body
        assert "`accounts`: 对象（1 个字段）" in body
        assert "`form`: {`amount`: 123, `reason`: 文本（6 字符）}" in body

    async def test_failed_approved_write_is_terminal(self):
        """[self-review] When an approved tool reports failure, the pending is resolved TERMINALLY (removed) — not
        left as a retryable awaiting record the (now button-less) card can never reach."""
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        reg = ToolRegistry()

        async def deploy(env):
            return ToolResult(ToolOutcome.FAILED, content="boom", is_error=True)

        reg.register("deploy", deploy, input_schema=DEPLOY_SCHEMA, description="deploy", requires_approval=True)
        backend = FakeLlmBackend(
            [
                tool_turn(index=0, id="c1", name="deploy", arguments_json='{"env":"prod"}'),
                text_turn("sorry, the deploy failed"),
            ]
        )
        agent = Agent(backend=backend, registry=reg, store=store, client=client, approvals=approvals)
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        card, _ = client.cards[0]
        approval_id = _extract_approval_id(card)
        sha = _extract_payload_sha256(card)
        await agent.handle_card_action(_action_event(approval_id, "approve", chat_id="oc_1", payload_sha256=sha))
        await _drain(agent)
        assert approvals._store == {}  # terminal: pending removed, not lingering as awaiting_confirmation
        assert len(backend.calls) == 2  # model resumed and got the failure to explain

    async def test_approved_write_needing_auth_creates_pending_authorization(self):
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        authorizations = InMemoryPendingAuthorizationStore()
        reg = ToolRegistry()

        async def deploy(env):
            return ToolResult(
                ToolOutcome.NEEDS_USER_AUTH,
                content="auth required",
                auth_scopes=("calendar:calendar",),
                is_error=True,
            )

        reg.register("deploy", deploy, input_schema=DEPLOY_SCHEMA, description="deploy", requires_approval=True)
        backend = FakeLlmBackend(
            [
                tool_turn(index=0, id="c1", name="deploy", arguments_json='{"env":"prod"}'),
                text_turn("this should not be generated before auth"),
            ]
        )
        seen_authorizations = []

        def authorize_url_builder(user, scopes, authorization=None):
            seen_authorizations.append((dict(user), tuple(scopes), authorization.authorization_id))
            return f"https://auth.example/authorize?state={authorization.authorization_id}"

        agent = Agent(
            backend=backend,
            registry=reg,
            store=store,
            client=client,
            approvals=approvals,
            authorizations=authorizations,
            auth_card_builder=lambda url: {"auth": url},
            authorize_url_builder=authorize_url_builder,
        )
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        card, _ = client.cards[0]
        approval_id = _extract_approval_id(card)
        sha = _extract_payload_sha256(card)

        await agent.handle_card_action(_action_event(approval_id, "approve", chat_id="oc_1", payload_sha256=sha))
        await _drain(agent)

        assert approvals._store == {}
        assert len(authorizations._store) == 1
        pending = next(iter(authorizations._store.values()))
        assert seen_authorizations == [({"open_id": "ou_tester"}, ("calendar:calendar",), pending.authorization_id)]
        assert client.cards[-1][0]["auth"].endswith(pending.authorization_id)
        assert client.patches and not _has_buttons(client.patches[-1][1])
        assert len(backend.calls) == 1
        history = await store.get("oc_1")
        results = [p for m in history if m.role == "tool" for p in m.content if isinstance(p, ToolResultPart)]
        assert any("Awaiting user authorization" in p.content for p in results)

    async def test_resume_raises_still_responds(self):
        """handle_card_action must ACK immediately and the background resume must swallow its own errors."""
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []

        reg = ToolRegistry()

        async def deploy(env):
            ran.append(env)
            return f"deployed to {env}"

        reg.register("deploy", deploy, input_schema=DEPLOY_SCHEMA, description="deploy", requires_approval=True)

        # First call: tool turn (triggers approval and suspension)
        # Second call (resumed _loop): backend raises to simulate a failure
        class _RaisingOnSecondCall:
            def __init__(self):
                self._call_count = 0
                self.calls = []

            def stream(self, *, messages, tools=(), system=None, **kwargs):
                self._call_count += 1
                self.calls.append({"messages": list(messages)})
                if self._call_count == 1:
                    script = tool_turn(index=0, id="c1", name="deploy", arguments_json='{"env":"prod"}')
                else:
                    raise RuntimeError("simulated backend failure during resume")

                async def _gen():
                    for chunk in script:
                        yield chunk

                return _gen()

        backend = _RaisingOnSecondCall()
        agent = Agent(backend=backend, registry=reg, store=store, client=client, approvals=approvals)

        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        card, _ = client.cards[0]
        approval_id = _extract_approval_id(card)
        sha = _extract_payload_sha256(card)

        # This must NOT raise — the immediate ACK is synchronous; the resume error is swallowed in the bg task.
        response = await agent.handle_card_action(
            _action_event(approval_id, "approve", chat_id="oc_1", payload_sha256=sha)
        )
        assert "toast" in response  # immediate ACK
        await _drain(agent)  # background resume raises internally; _drain must not propagate it

        # The tool itself ran (dispatch happened before the resumed _loop raised)
        assert ran == ["prod"]
        # The clicked card is still patched with the decided card (buttons removed), despite the resume error
        assert client.patches and not _has_buttons(client.patches[-1][1])

    async def test_invalid_decision_info_toast(self):
        """FIX 1: a card action with an unrecognised decision value gets an info toast, no tool dispatch,
        and — critically — the PendingApproval is NOT consumed so the user can retry with a valid decision."""
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []
        agent, backend = _agent_with_deploy(client, store, approvals, ran)

        # Prime a pending approval
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        card, _ = client.cards[0]
        approval_id = _extract_approval_id(card)
        sha = _extract_payload_sha256(card)

        # Send a card action with a bogus decision value
        body = {
            "action": {"value": {"__approval__": approval_id, "decision": "bogus", "payload_sha256": sha}},
            "message": {"chat_id": "oc_1", "message_id": "om_in"},
        }
        event = SimpleNamespace(event_type="card.action.trigger", body=body)
        response = await agent.handle_card_action(event)

        # Must return the invalid-decision info toast
        assert response == {"toast": {"type": "info", "content": "无效的确认操作"}}
        # Tool must NOT have run
        assert ran == []
        # The pending approval must still be in the store (not consumed into execution)
        # i.e. backend was called exactly once (the initial run), no second call
        assert len(backend.calls) == 1

        # KEY assertion: the PendingApproval must still be retrievable — bogus decision
        # must NOT have consumed (popped) it.  Prove this by successfully approving now.
        response2 = await agent.handle_card_action(
            _action_event(approval_id, "approve", chat_id="oc_1", payload_sha256=sha)
        )
        assert "toast" in response2  # immediate ACK
        await _drain(agent)
        assert ran == ["prod"]  # tool ran on the retry-approve (decided in the background)

    async def test_clicker_must_be_initiator(self):
        """High: in a group chat, a non-initiator cannot confirm someone else's write (least-privilege)."""
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []
        agent, _ = _agent_with_deploy(client, store, approvals, ran)
        await agent.run(_text_event("deploy prod", chat_id="oc_1", open_id="ou_alice"))
        approval_id = _extract_approval_id(client.cards[0][0])
        # Bob (a different operator) tries to confirm Alice's write
        response = await agent.handle_card_action(
            _action_event(approval_id, "approve", chat_id="oc_1", open_id="ou_bob")
        )
        await _drain(agent)
        assert response["toast"] == {"type": "info", "content": "处理中…"}
        assert ran == []  # the write did NOT execute

    async def test_fail_closed_without_identity(self):
        """High: a write whose requester cannot be identified must NOT become a confirmable approval."""
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []
        agent, backend = _agent_with_deploy(client, store, approvals, ran)
        # An inbound event with NO sender → no identifiable initiator
        evt = SimpleNamespace(
            event_type="im.message.receive_v1",
            body={
                "message": {
                    "chat_id": "oc_1",
                    "message_id": "om_in",
                    "message_type": "text",
                    "content": json.dumps({"text": "deploy prod"}),
                }
            },
        )
        await agent.run(evt)
        assert client.cards == []  # fail-closed: no confirmation card sent
        assert ran == []
        assert len(backend.calls) == 2  # model got a tool error and produced a follow-up turn

    async def test_card_send_failure_leaves_no_dangling_pending(self):
        """High: persist-then-send — if the card can't be delivered, the just-stored pending is explicitly
        cancelled (no dangling pending the user could never confirm) and the model gets a tool error and recovers."""
        client = _ApprovalRecordingClient()

        async def _boom(*a, **k):
            raise RuntimeError("send failed")

        client.im.send = _boom  # type: ignore[method-assign]
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []
        agent, backend = _agent_with_deploy(client, store, approvals, ran)
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        assert client.cards == []  # nothing delivered
        assert ran == []  # write not started
        assert approvals._store == {}  # persisted-then-cancelled: no dangling pending left behind
        assert len(backend.calls) == 2  # model recovered with a follow-up turn (no hang on a dangling pending)

    async def test_pending_is_persisted_before_card_is_sent(self):
        """High (ordering): the pending must be in the store BEFORE the card is delivered, so a click can never hit
        an empty store ("没有待处理的确认请求"). Snapshot the store at the instant send() is called."""
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        client = _ApprovalRecordingClient()
        original_send = client.im.send
        store_at_send: list[dict] = []

        async def _snapshotting_send(*a, **k):
            store_at_send.append(dict(approvals._store))  # state of the store at the moment of delivery
            return await original_send(*a, **k)

        client.im.send = _snapshotting_send  # type: ignore[method-assign]
        ran = []
        agent, backend = _agent_with_deploy(client, store, approvals, ran)
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        assert len(client.cards) == 1  # card delivered, turn suspended awaiting confirmation
        assert store_at_send and store_at_send[0]  # the pending was already persisted WHEN send was invoked
        assert len(approvals._store) == 1  # still pending (awaiting the click)

    async def test_reject_is_claim_gated(self):
        """High: reject goes through the same atomic claim as approve, so a concurrent approve can't be
        double-processed. Pre-claiming (simulating approve winning) makes a later reject a no-op, not a fresh
        rejection."""
        approvals = InMemoryPendingApprovalStore()
        engine = DefaultApprovalEngine(approvals=approvals)
        ap = PendingApproval(
            approval_id="ap1", session_id="s", tool_call_id="c", tool_name="deploy", arguments={}, payload_sha256="ph"
        )
        await engine.on_request(ap)
        won = await approvals.claim("ap1", expected_payload_sha256="ph")  # approve wins the claim first
        assert won is ClaimResult.CLAIMED

        async def _dispatch(name, args):
            return ToolResult(ToolOutcome.COMPLETED, content="x")

        outcome = await engine.on_decision("ap1", "reject", expected_payload_sha256="ph", dispatch=_dispatch)
        assert outcome.status is not ApprovalStatus.REJECTED  # claim-gated: not a second, fresh decision

    async def test_on_cancel_removes_pending_and_audits(self):
        """on_cancel removes a not-yet-decided pending (no claim required) and records a 'cancel' audit event;
        cancelling an unknown id is a no-op."""
        events: list[tuple] = []

        class _Audit:
            def append(
                self, event_type, *, key, approval=None, event_id=None, message_id=None, outcome="ok", error=None
            ):
                events.append((event_type, key, outcome))

        approvals = InMemoryPendingApprovalStore()
        engine = DefaultApprovalEngine(approvals=approvals, audit=_Audit())
        ap = PendingApproval(
            approval_id="ap1", session_id="s", tool_call_id="c", tool_name="deploy", arguments={}, payload_sha256="ph"
        )
        await engine.on_request(ap)
        assert "ap1" in approvals._store
        await engine.on_cancel("ap1")
        assert approvals._store == {}  # removed, not frozen
        assert ("cancel", "ap1", "ok") in events  # auditable rollback
        await engine.on_cancel("nope")  # unknown id: no error, no audit


# ===========================================================================
# Adapter-driven loop integration: REAL adapter backend + injected fake SDK
# ===========================================================================
#
# These tests prove that adapter.stream() -> accumulate_stream -> loop -> _finalize
# works end-to-end for both AnthropicBackend and OpenAIBackend.  The injected-client
# path avoids importing the real anthropic/openai SDK entirely, so these run and pass
# WITHOUT the extras installed.


class _IntegrationRecordingClient:
    """Minimal stand-in for FeishuClient exposing .im.reply."""

    def __init__(self):
        self.replies: list[tuple] = []

        class _IM:
            async def reply(_self, message_id, content, *, msg_type="text", reply_in_thread=None, uuid=None):
                self.replies.append((message_id, content, msg_type))
                return {"message_id": "om_reply"}

        self.im = _IM()


# --- Anthropic fake SDK client (text-only stream) ---


def _anthropic_integration_text_events():
    return [
        _ns(type="message_start"),
        _ns(type="content_block_start", index=0, content_block=_ns(type="text")),
        _ns(type="content_block_delta", index=0, delta=_ns(type="text_delta", text="Hello")),
        _ns(type="content_block_delta", index=0, delta=_ns(type="text_delta", text=" world")),
        _ns(type="content_block_stop", index=0),
        _ns(type="message_delta", delta=_ns(stop_reason="end_turn"), usage=_ns(output_tokens=2)),
        _ns(type="message_stop"),
    ]


class _AnthropicFakeStream:
    """Async context manager + async iterator yielding pre-canned events."""

    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for ev in self._events:
            yield ev

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _AnthropicFakeMessages:
    def __init__(self):
        self.calls: list[dict] = []

    def stream(self, **kwargs) -> _AnthropicFakeStream:
        self.calls.append(kwargs)
        return _AnthropicFakeStream(_anthropic_integration_text_events())


class _AnthropicFakeClient:
    def __init__(self):
        self.messages = _AnthropicFakeMessages()


# --- OpenAI fake SDK client (text-only stream) ---


def _openai_integration_text_chunks():
    def _chunk(*, content=None, finish_reason=None, usage=None):
        delta = _ns(content=content, tool_calls=None)
        choice = _ns(delta=delta, finish_reason=finish_reason)
        return _ns(choices=[choice], usage=usage)

    return [
        _chunk(content="Hello"),
        _chunk(content=" world"),
        _chunk(finish_reason="stop"),
        _chunk(usage=_ns(prompt_tokens=4, completion_tokens=2, total_tokens=6)),
    ]


class _OpenAIFakeAsyncIter:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class _OpenAIFakeCompletions:
    def __init__(self):
        self.calls: list[dict] = []

    async def create(self, **kwargs) -> _OpenAIFakeAsyncIter:
        self.calls.append(kwargs)
        return _OpenAIFakeAsyncIter(_openai_integration_text_chunks())


class _OpenAIFakeChat:
    def __init__(self):
        self.completions = _OpenAIFakeCompletions()


class _OpenAIFakeClient:
    def __init__(self):
        self.chat = _OpenAIFakeChat()


def _anthropic_backend():
    fake_sdk = _AnthropicFakeClient()
    return AnthropicBackend(client=fake_sdk, model="claude-x"), fake_sdk


def _openai_backend():
    fake_sdk = _OpenAIFakeClient()
    return OpenAIBackend(client=fake_sdk, model="gpt-x"), fake_sdk


class TestAdapterLoopIntegration:
    @pytest.mark.parametrize("make_backend", [_anthropic_backend, _openai_backend], ids=["anthropic", "openai"])
    async def test_backend_drives_agent_loop(self, make_backend):
        """REAL provider backend with injected fake SDK → Agent.run → im.reply called with expected text."""
        backend, _ = make_backend()
        feishu_client = _IntegrationRecordingClient()
        agent = Agent(
            backend=backend,
            registry=ToolRegistry(),
            client=feishu_client,
            store=InMemorySessionStore(),
        )

        await agent.run(_text_event("hi", message_id="om_in", chat_id="oc_1"))

        assert feishu_client.replies == [("om_in", "Hello world", "text")]

    @pytest.mark.parametrize(
        "make_backend, read_system",
        [
            (_anthropic_backend, lambda sdk: sdk.messages.calls[0].get("system")),
            (
                _openai_backend,
                lambda sdk: next(
                    m["content"] for m in sdk.chat.completions.calls[0]["messages"] if m.get("role") == "system"
                ),
            ),
        ],
        ids=["anthropic", "openai"],
    )
    async def test_backend_forwards_system_prompt(self, make_backend, read_system):
        """System prompt reaches the provider SDK (top-level kwarg for Anthropic, system message for OpenAI)."""
        backend, fake_sdk = make_backend()
        agent = Agent(
            backend=backend,
            registry=ToolRegistry(),
            client=_IntegrationRecordingClient(),
            store=InMemorySessionStore(),
            system="You are a helpful assistant.",
        )

        await agent.run(_text_event("hi"))

        assert read_system(fake_sdk) == "You are a helpful assistant."

    async def test_turn_context_is_sent_outside_static_system_prompt(self):
        backend, fake_sdk = _openai_backend()
        store = InMemorySessionStore()
        agent = Agent(
            backend=backend,
            registry=ToolRegistry(),
            client=_IntegrationRecordingClient(),
            store=store,
            system="Static system prompt.",
            turn_context=lambda _event, _timezone: "Current datetime: 2026-07-04T18:55:00+08:00",
            timezone="Asia/Shanghai",
        )

        await agent.run(_text_event("hi", chat_id="oc_cache"))

        request = fake_sdk.chat.completions.calls[0]
        assert next(m["content"] for m in request["messages"] if m["role"] == "system") == "Static system prompt."
        assert request["messages"][-1] == {
            "role": "user",
            "content": "hi\n\nCurrent datetime: 2026-07-04T18:55:00+08:00",
        }
        persisted = await store.get("oc_cache")
        assert persisted[0] == Message(role="user", content=[TextPart(text="hi")])

    async def test_turn_context_remains_available_after_tool_call(self):
        reg = ToolRegistry()

        async def weather():
            return "sunny"

        reg.register("weather", weather, input_schema={"type": "object", "properties": {}}, description="d")
        backend = FakeLlmBackend(
            [
                tool_turn(index=0, id="c_weather", name="weather", arguments_json="{}"),
                text_turn("done"),
            ]
        )
        store = InMemorySessionStore()
        agent = Agent(
            backend=backend,
            registry=reg,
            client=_IntegrationRecordingClient(),
            store=store,
            system="Static system prompt.",
            turn_context=lambda _event, _timezone: "Current datetime: 2026-07-04T19:05:00+08:00",
            timezone="Asia/Shanghai",
        )

        await agent.run(_text_event("hi", chat_id="oc_tool_ctx"))

        second_call_user = next(message for message in backend.calls[1]["messages"] if message.role == "user")
        assert second_call_user.content == [
            TextPart(text="hi"),
            TextPart(text="\n\nCurrent datetime: 2026-07-04T19:05:00+08:00"),
        ]
        persisted = await store.get("oc_tool_ctx")
        assert persisted[0] == Message(role="user", content=[TextPart(text="hi")])


# ===========================================================================
# Cross-adapter parity: both providers translate equivalent streams identically
# ===========================================================================


def _parity_anthropic_tool_events():
    """Simulate an Anthropic streaming response with a single tool-use call."""
    return [
        _ns(type="message_start"),
        _ns(type="content_block_start", index=0, content_block=_ns(type="tool_use", id="t1", name="weather")),
        _ns(type="content_block_delta", index=0, delta=_ns(type="input_json_delta", partial_json='{"city"')),
        _ns(type="content_block_delta", index=0, delta=_ns(type="input_json_delta", partial_json=':"sh"}')),
        _ns(type="content_block_stop", index=0),
        _ns(type="message_delta", delta=_ns(stop_reason="tool_use"), usage=_ns(output_tokens=1)),
        _ns(type="message_stop"),
    ]


def _parity_anthropic_text_events():
    """Simulate an Anthropic streaming response with plain text."""
    return [
        _ns(type="message_start"),
        _ns(type="content_block_start", index=0, content_block=_ns(type="text", id=None, name=None)),
        _ns(type="content_block_delta", index=0, delta=_ns(type="text_delta", text="Hello ")),
        _ns(type="content_block_delta", index=0, delta=_ns(type="text_delta", text="world")),
        _ns(type="content_block_stop", index=0),
        _ns(type="message_delta", delta=_ns(stop_reason="end_turn"), usage=_ns(output_tokens=2)),
        _ns(type="message_stop"),
    ]


def _parity_openai_tool_chunks():
    """Simulate an OpenAI Chat Completions streaming response with a single tool call."""

    def tc(index, *, id=None, name=None, arguments=None):
        return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))

    def chunk(*, tool_calls=None, finish_reason=None):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=tool_calls), finish_reason=finish_reason)
            ],
            usage=None,
        )

    return [
        chunk(tool_calls=[tc(0, id="t1", name="weather", arguments='{"city"')]),
        chunk(tool_calls=[tc(0, arguments=':"sh"}')]),
        chunk(finish_reason="tool_calls"),
    ]


def _parity_openai_text_chunks():
    """Simulate an OpenAI Chat Completions streaming response with plain text."""

    def chunk(*, content=None, finish_reason=None):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=None), finish_reason=finish_reason)
            ],
            usage=None,
        )

    return [
        chunk(content="Hello "),
        chunk(content="world"),
        chunk(finish_reason="stop"),
    ]


def _anthropic_stop_only_events(stop_reason):
    """Minimal Anthropic stream that emits only the given terminal stop_reason."""
    return [
        _ns(type="message_start"),
        _ns(type="message_delta", delta=_ns(stop_reason=stop_reason), usage=None),
        _ns(type="message_stop"),
    ]


def _openai_stop_only_chunks(finish_reason):
    """Minimal OpenAI stream that emits only the given terminal finish_reason."""
    return [
        SimpleNamespace(
            choices=[
                SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=None), finish_reason=finish_reason)
            ],
            usage=None,
        )
    ]


class _ParityAnthropicClient:
    """anthropic.AsyncAnthropic stand-in replaying a scripted list of provider events."""

    def __init__(self, events):
        self._events = events
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        return _AnthropicFakeStream(self._events)


class _ParityOpenAIClient:
    """openai.AsyncOpenAI stand-in replaying a scripted list of chunk objects."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        return _OpenAIFakeAsyncIter(self._chunks)


async def _anthropic_result(events):
    """Drive the REAL AnthropicBackend over scripted events and accumulate the StreamResult."""
    backend = AnthropicBackend(client=_ParityAnthropicClient(events), model="claude-x")
    msgs = [Message(role="user", content=[TextPart(text="q")])]
    return await accumulate_stream(backend.stream(messages=msgs))


async def _openai_result(chunks):
    """Drive the REAL OpenAIBackend over scripted chunks and accumulate the StreamResult."""
    backend = OpenAIBackend(client=_ParityOpenAIClient(chunks), model="gpt-x")
    msgs = [Message(role="user", content=[TextPart(text="q")])]
    return await accumulate_stream(backend.stream(messages=msgs))


class TestAdapterParity:
    # The per-reason mapping table (end_turn/tool_use/max_tokens/refusal/unknown) is already
    # covered by tests/agent/adapters/test_anthropic.py and test_openai.py via the public
    # stream(). Only the two reasons UNIQUE to each provider are pinned here, and through the
    # same public path (backend.stream -> accumulate_stream), not the private mapping helpers.

    async def test_anthropic_stop_sequence(self):
        """stop_sequence is a clean completion in Anthropic — must surface as END_TURN (not OTHER)."""
        result = await _anthropic_result(_anthropic_stop_only_events("stop_sequence"))
        assert result.stop_reason == StopReason.END_TURN

    async def test_openai_function_call(self):
        """function_call is the legacy OpenAI tool alias — must surface as TOOL_USE."""
        result = await _openai_result(_openai_stop_only_chunks("function_call"))
        assert result.stop_reason == StopReason.TOOL_USE

    async def test_parity_tool_call(self):
        """The headline parity spec: both providers must produce an identical StreamResult
        for an equivalent tool-use turn (same id, name, arguments JSON, stop_reason),
        driven through the public backend.stream() of each adapter."""
        a = await _anthropic_result(_parity_anthropic_tool_events())
        o = await _openai_result(_parity_openai_tool_chunks())
        expected = ToolCall(id="t1", name="weather", arguments='{"city":"sh"}')
        assert a.tool_calls == [expected], f"Anthropic tool_calls mismatch: {a.tool_calls}"
        assert o.tool_calls == [expected], f"OpenAI tool_calls mismatch: {o.tool_calls}"
        assert a.stop_reason == StopReason.TOOL_USE, f"Anthropic stop_reason: {a.stop_reason}"
        assert o.stop_reason == StopReason.TOOL_USE, f"OpenAI stop_reason: {o.stop_reason}"
        assert a.stop_reason == o.stop_reason, "Providers returned different stop_reason"

    async def test_parity_text(self):
        """Text-stream parity: both providers must produce identical text and END_TURN,
        driven through the public backend.stream() of each adapter."""
        a = await _anthropic_result(_parity_anthropic_text_events())
        o = await _openai_result(_parity_openai_text_chunks())
        assert a.text == "Hello world", f"Anthropic text: {a.text!r}"
        assert o.text == "Hello world", f"OpenAI text: {o.text!r}"
        assert a.tool_calls == [], f"Anthropic unexpected tool_calls: {a.tool_calls}"
        assert o.tool_calls == [], f"OpenAI unexpected tool_calls: {o.tool_calls}"
        assert a.stop_reason == StopReason.END_TURN, f"Anthropic stop_reason: {a.stop_reason}"
        assert o.stop_reason == StopReason.END_TURN, f"OpenAI stop_reason: {o.stop_reason}"
        assert a.stop_reason == o.stop_reason, "Providers returned different stop_reason"

    async def test_public_exports_present(self):
        """Only agent core primitives are re-exported from feishu.agent."""
        import feishu.agent as agent

        required = [
            "Agent",
            "Tool",
            "ToolRegistry",
            "ToolValidationError",
            "ToolOutcome",
            "ToolResult",
        ]
        for name in required:
            assert hasattr(agent, name), f"feishu.agent is missing public export: {name!r}"

        assert not hasattr(agent, "create_calendar_event")
        assert not hasattr(agent, "SqliteSessionStore")
