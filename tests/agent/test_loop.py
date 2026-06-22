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

import json
from types import SimpleNamespace

import pytest

from feishu.agent.adapters.anthropic import AnthropicBackend
from feishu.agent.adapters.openai import OpenAIBackend
from feishu.agent.llm import (
    Message,
    MessageStop,
    StopReason,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallDelta,
)
from feishu.agent.loop import (
    Agent,
    StreamResult,
    accumulate_stream,
    session_id_for,
    user_message_from_event,
)
from feishu.agent.session import InMemoryPendingApprovalStore, InMemorySessionStore
from feishu.agent.tools import ToolRegistry
from tests._fakes import FakeLlmBackend, text_turn, tool_turn

SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
DEPLOY_SCHEMA = {"type": "object", "properties": {"env": {"type": "string"}}, "required": ["env"]}


def _ns(**kw):
    return SimpleNamespace(**kw)


def _text_event(text="hi", *, message_id="om_in", chat_id="oc_1"):
    body = {
        "message": {
            "chat_id": chat_id,
            "message_id": message_id,
            "message_type": "text",
            "content": json.dumps({"text": text}),
        }
    }
    return SimpleNamespace(event_type="im.message.receive_v1", body=body)


async def _agen(items):
    for i in items:
        yield i


# ===========================================================================
# Agent loop: plain replies, tool dispatch, iteration bounds, streaming
# ===========================================================================


class _LoopRecordingClient:
    """Minimal stand-in for FeishuClient exposing .im.reply and .stream_card."""

    def __init__(self):
        self.replies = []
        self.stream_card_calls = []

        class _IM:
            async def reply(_self, message_id, content, *, msg_type="text", reply_in_thread=None, uuid=None):
                self.replies.append((message_id, content, msg_type))
                return {"message_id": "om_reply"}

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

    def test_non_text_raw_content(self):
        raw = json.dumps({"image_key": "img_x"})
        ev = self._event({"message": {"message_type": "image", "content": raw}})
        msg = user_message_from_event(ev)
        assert msg.content[0].text == raw

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
        outer = self

        class _IM:
            async def reply(self, message_id, content, *, msg_type="text", reply_in_thread=None, uuid=None):
                outer.replies.append((message_id, content, msg_type))
                return {"message_id": "om_reply"}

            async def send(self, receive_id, content, *, msg_type=None, receive_id_type=None, uuid=None):
                outer.cards.append((content, receive_id))
                return {"message_id": "om_card"}

        self.im = _IM()


def _action_event(approval_id, decision, chat_id="oc_1"):
    body = {
        "action": {"value": {"__approval__": approval_id, "decision": decision}},
        "message": {"chat_id": chat_id, "message_id": "om_in"},
    }
    return SimpleNamespace(event_type="card.action.trigger", body=body)


def _agent_with_deploy(client, store, approvals, ran):
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
    return Agent(backend=backend, registry=reg, store=store, client=client, approvals=approvals), backend


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
        response = await agent.handle_card_action(_action_event(approval_id, "approve", chat_id="oc_1"))
        assert ran == ["prod"]  # tool ran on approval
        assert client.replies[-1][1] == "deployment complete"  # loop resumed + finalized
        assert "toast" in response and "card" in response  # synchronous response
        # buttons removed on the returned card
        assert not _has_buttons(response["card"])

    async def test_reject_skips_tool(self):
        client = _ApprovalRecordingClient()
        store = InMemorySessionStore()
        approvals = InMemoryPendingApprovalStore()
        ran = []
        agent, backend = _agent_with_deploy(client, store, approvals, ran)
        await agent.run(_text_event("deploy prod", chat_id="oc_1"))
        card, _ = client.cards[0]
        approval_id = _extract_approval_id(card)
        response = await agent.handle_card_action(_action_event(approval_id, "reject", chat_id="oc_1"))
        assert ran == []  # tool did NOT run
        # the model still gets to react -> backend called a second time -> a reply emitted
        assert len(backend.calls) == 2
        assert "toast" in response

    async def test_unknown_info_toast(self):
        client = _ApprovalRecordingClient()
        agent, _ = _agent_with_deploy(client, InMemorySessionStore(), InMemoryPendingApprovalStore(), [])
        response = await agent.handle_card_action(_action_event("does-not-exist", "approve"))
        assert response["toast"]["type"] == "info"

    async def test_resume_raises_still_responds(self):
        """handle_card_action must return {toast, card} even if the resumed run raises."""
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

        # This must NOT raise — the exception should be caught internally
        response = await agent.handle_card_action(_action_event(approval_id, "approve", chat_id="oc_1"))

        # The tool itself ran (dispatch happened before _loop)
        assert ran == ["prod"]
        # The synchronous Feishu response must always be a {toast, card} dict
        assert "toast" in response
        assert "card" in response
        # The card must have buttons removed (decided card, not approval card)
        assert not _has_buttons(response["card"])

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

        # Send a card action with a bogus decision value
        body = {
            "action": {"value": {"__approval__": approval_id, "decision": "bogus"}},
            "message": {"chat_id": "oc_1", "message_id": "om_in"},
        }
        event = SimpleNamespace(event_type="card.action.trigger", body=body)
        response = await agent.handle_card_action(event)

        # Must return the invalid-decision info toast
        assert response == {"toast": {"type": "info", "content": "invalid decision"}}
        # Tool must NOT have run
        assert ran == []
        # The pending approval must still be in the store (not consumed into execution)
        # i.e. backend was called exactly once (the initial run), no second call
        assert len(backend.calls) == 1

        # KEY assertion: the PendingApproval must still be retrievable — bogus decision
        # must NOT have consumed (popped) it.  Prove this by successfully approving now.
        response2 = await agent.handle_card_action(_action_event(approval_id, "approve", chat_id="oc_1"))
        assert ran == ["prod"]  # tool ran on the retry-approve
        assert response2["toast"]["type"] == "success"


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
        """All names listed in the brief's __all__ must be importable from feishu.agent."""
        import feishu.agent as agent

        required = [
            "LlmBackend",
            "Message",
            "TextPart",
            "ToolUsePart",
            "ToolResultPart",
            "ContentPart",
            "ToolSpec",
            "Role",
            "StopReason",
            "TextDelta",
            "ToolCallDelta",
            "MessageStop",
            "StreamChunk",
            "ToolCall",
            "Tool",
            "ToolRegistry",
            "ToolValidationError",
            "SessionStore",
            "InMemorySessionStore",
            "PendingApproval",
            "PendingApprovalStore",
            "InMemoryPendingApprovalStore",
            "Agent",
            "StreamResult",
            "accumulate_stream",
            "session_id_for",
            "user_message_from_event",
            "register_agent",
        ]
        for name in required:
            assert hasattr(agent, name), f"feishu.agent is missing public export: {name!r}"
