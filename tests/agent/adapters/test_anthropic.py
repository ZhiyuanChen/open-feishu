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

from __future__ import annotations

from types import SimpleNamespace

import pytest

from feishu.agent.adapters.anthropic import AnthropicBackend
from feishu.agent.llm import (
    LlmBackend,
    Message,
    MessageStop,
    StopReason,
    TextDelta,
    TextPart,
    ToolCallDelta,
    ToolResultPart,
    ToolSpec,
    ToolUsePart,
)


def _ns(**kw):
    return SimpleNamespace(**kw)


class _FakeAnthropicClient:
    """Stand-in for anthropic.AsyncAnthropic.

    Records the params the backend passes to ``messages.stream(...)`` and replays
    a scripted list of provider events through the async-context-manager + async
    iterator protocol the real SDK exposes.
    """

    def __init__(self, events):
        self._events = events
        self.last_params: dict | None = None
        self.messages = self._Messages(self)

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def stream(self, **params):
            self._outer.last_params = params
            return _FakeAnthropicClient._StreamCtx(self._outer._events)

    class _StreamCtx:
        def __init__(self, events):
            self._events = events

        async def __aenter__(self):
            events = self._events

            async def _gen():
                for ev in events:
                    yield ev

            return _gen()

        async def __aexit__(self, *exc):
            return False


def _text_stream_events():
    # message_start -> text block -> two text deltas -> block stop -> message_delta(end_turn) -> message_stop
    return [
        _ns(type="message_start"),
        _ns(type="content_block_start", index=0, content_block=_ns(type="text")),
        _ns(type="content_block_delta", index=0, delta=_ns(type="text_delta", text="foo")),
        _ns(type="content_block_delta", index=0, delta=_ns(type="text_delta", text="bar")),
        _ns(type="content_block_stop", index=0),
        _ns(type="message_delta", delta=_ns(stop_reason="end_turn"), usage=_ns(output_tokens=3)),
        _ns(type="message_stop"),
    ]


def _tool_stream_events():
    return [
        _ns(type="message_start"),
        _ns(type="content_block_start", index=0, content_block=_ns(type="tool_use", id="toolu_1", name="weather")),
        _ns(type="content_block_delta", index=0, delta=_ns(type="input_json_delta", partial_json='{"ci')),
        _ns(type="content_block_delta", index=0, delta=_ns(type="input_json_delta", partial_json='ty":"sh"}')),
        _ns(type="content_block_stop", index=0),
        _ns(type="message_delta", delta=_ns(stop_reason="tool_use"), usage=_ns(output_tokens=7)),
        _ns(type="message_stop"),
    ]


def _stop_only_events(stop_reason):
    return [
        _ns(type="message_start"),
        _ns(type="message_delta", delta=_ns(stop_reason=stop_reason), usage=None),
        _ns(type="message_stop"),
    ]


@pytest.fixture
def make_backend():
    """Build an AnthropicBackend over a fake client replaying the given provider events."""

    def _make(events):
        client = _FakeAnthropicClient(events)
        backend = AnthropicBackend(client=client, model="claude-3-5-sonnet-latest")
        return backend, client

    return _make


async def _drain(backend, *, messages=None, tools=(), system=None):
    msgs = messages if messages is not None else [Message(role="user", content=[TextPart(text="q")])]
    return [c async for c in backend.stream(messages=msgs, tools=tools, system=system)]


class TestStreamTranslation:
    """Provider stream events surface as neutral StreamChunks through the public stream()."""

    async def test_text_deltas_then_stop(self, make_backend):
        backend, _ = make_backend(_text_stream_events())
        chunks = await _drain(backend)
        assert chunks == [
            TextDelta(text="foo"),
            TextDelta(text="bar"),
            MessageStop(stop_reason=StopReason.END_TURN, usage={"output_tokens": 3}),
        ]

    async def test_tool_deltas_reassemble(self, make_backend):
        backend, _ = make_backend(_tool_stream_events())
        chunks = await _drain(backend)
        tool_deltas = [c for c in chunks if isinstance(c, ToolCallDelta)]
        assert tool_deltas[0] == ToolCallDelta(index=0, id="toolu_1", name="weather", arguments="")
        # fragmented input json is forwarded verbatim for the accumulator to reassemble
        assert "".join(d.arguments for d in tool_deltas) == '{"city":"sh"}'
        assert chunks[-1] == MessageStop(stop_reason=StopReason.TOOL_USE, usage={"output_tokens": 7})

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("end_turn", StopReason.END_TURN),
            ("tool_use", StopReason.TOOL_USE),
            ("max_tokens", StopReason.MAX_TOKENS),
            ("refusal", StopReason.REFUSAL),
            ("content_filter", StopReason.OTHER),  # unknown provider reason -> OTHER
            (None, StopReason.OTHER),
        ],
    )
    async def test_stop_reason_maps_to_enum(self, make_backend, raw, expected):
        backend, _ = make_backend(_stop_only_events(raw))
        chunks = await _drain(backend)
        assert chunks[-1] == MessageStop(stop_reason=expected, usage=None)


class TestRequestPayload:
    """The wire request the backend builds is the provider contract worth pinning."""

    async def test_tools_and_messages_reach_provider(self, make_backend):
        backend, client = make_backend(_text_stream_events())
        specs = [ToolSpec(name="weather", description="d", input_schema={"type": "object"})]
        msgs = [
            Message(role="user", content=[TextPart(text="hi")]),
            Message(role="assistant", content=[ToolUsePart(id="t1", name="weather", arguments={"city": "sh"})]),
            Message(role="tool", content=[ToolResultPart(tool_call_id="t1", content="sunny")]),
        ]
        await _drain(backend, messages=msgs, tools=specs, system="be nice")

        params = client.last_params
        assert params["model"] == "claude-3-5-sonnet-latest"
        assert params["system"] == "be nice"
        # tools translate to anthropic's input_schema shape
        assert params["tools"] == [{"name": "weather", "description": "d", "input_schema": {"type": "object"}}]
        out = params["messages"]
        assert out[0] == {"role": "user", "content": [{"type": "text", "text": "hi"}]}
        assert out[1]["role"] == "assistant"
        assert out[1]["content"][0] == {"type": "tool_use", "id": "t1", "name": "weather", "input": {"city": "sh"}}
        # provider wire fact: a tool result is sent as a user-role tool_result block
        assert out[2]["role"] == "user"
        assert out[2]["content"][0] == {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "sunny",
            "is_error": False,
        }


def test_backend_satisfies_llmbackend_protocol():
    pytest.importorskip("anthropic")
    backend = AnthropicBackend(client=object(), model="claude-3-5-sonnet-latest")
    assert isinstance(backend, LlmBackend)
