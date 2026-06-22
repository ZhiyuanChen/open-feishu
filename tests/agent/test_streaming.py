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

"""Tests for feishu.agent.streaming.stream_text.

Covers:
- OpenAI-shaped async chunk iterator (text-only, tool-call-skipping, stop-skipping)
- Anthropic-shaped async event iterator (text-only, tool-call-skipping)
- Normalized StreamChunk async iterator (TextDelta passthrough, ToolCallDelta/MessageStop skipped)
- Synchronous iterable input (OpenAI-shaped and Anthropic-shaped)
- Anthropic context-manager style (object with __aenter__ but no __aiter__)
- Empty stream yields nothing
- TypeError for unsupported input
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from feishu.agent.llm import MessageStop, StopReason, TextDelta, ToolCallDelta
from feishu.agent.streaming import stream_text

# ---------------------------------------------------------------------------
# Fake stream helpers
# ---------------------------------------------------------------------------


def _oai_chunk(*, content=None, finish_reason=None, tool_calls=None):
    """Build a SimpleNamespace mimicking an OpenAI ChatCompletionChunk."""
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def _oai_text_chunks(*texts: str) -> list:
    """OpenAI chunk list: text deltas followed by a stop finish_reason."""
    chunks = [_oai_chunk(content=t) for t in texts]
    chunks.append(_oai_chunk(finish_reason="stop"))
    return chunks


def _anthropic_text_events(*texts: str) -> list:
    """Anthropic SSE event list: text_delta events plus bookend start/stop events."""
    events = [
        SimpleNamespace(type="message_start"),
        SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="text")),
    ]
    for text in texts:
        events.append(
            SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(type="text_delta", text=text))
        )
    events += [
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=len(texts)),
        ),
        SimpleNamespace(type="message_stop"),
    ]
    return events


async def _agen(items):
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Fake Anthropic context-manager style (has __aenter__, no __aiter__)
# ---------------------------------------------------------------------------


class _FakeAnthropicStreamManager:
    """Mimic anthropic.AsyncMessageStreamManager: async context manager, not directly iterable."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return _agen(self._events)

    async def __aexit__(self, *exc):
        return False


# A normalized StreamChunk stream carrying the same texts as the provider helpers.
def _normalized_text_chunks(*texts: str) -> list:
    return [TextDelta(text=t) for t in texts] + [MessageStop(stop_reason=StopReason.END_TURN)]


# Each builder turns ("foo", "bar") into a full provider-shaped stream of those texts.
PROVIDER_BUILDERS = pytest.mark.parametrize(
    "build",
    [
        pytest.param(_oai_text_chunks, id="openai"),
        pytest.param(_anthropic_text_events, id="anthropic"),
        pytest.param(_normalized_text_chunks, id="normalized"),
    ],
)


# ---------------------------------------------------------------------------
# Text passthrough across every supported provider shape
# ---------------------------------------------------------------------------


class TestTextPassthrough:
    @PROVIDER_BUILDERS
    async def test_yields_text_in_order(self, build):
        tokens = [t async for t in stream_text(_agen(build("Hello", " world")))]
        assert tokens == ["Hello", " world"]

    @PROVIDER_BUILDERS
    async def test_empty_yields_nothing(self, build):
        tokens = [t async for t in stream_text(_agen([]))]
        assert tokens == []


# ---------------------------------------------------------------------------
# Tool-call / control events are skipped, only text survives
# ---------------------------------------------------------------------------


def _oai_tool_call(name="tool", arguments="{}"):
    return SimpleNamespace(index=0, id="c1", function=SimpleNamespace(name=name, arguments=arguments))


def _oai_mixed_stream():
    return [
        _oai_chunk(content="before"),
        _oai_chunk(tool_calls=[_oai_tool_call()]),
        _oai_chunk(content="after"),
        _oai_chunk(finish_reason="stop"),
    ]


def _oai_tool_only_stream():
    return [
        _oai_chunk(tool_calls=[_oai_tool_call("weather", '{"city":"sh"}')], finish_reason=None),
        _oai_chunk(finish_reason="tool_calls"),
    ]


def _anthropic_tool_only_stream():
    return [
        SimpleNamespace(type="message_start"),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="tool_use", id="t1", name="weather"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"city":"sh"}'),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="tool_use"),
            usage=SimpleNamespace(output_tokens=1),
        ),
        SimpleNamespace(type="message_stop"),
    ]


def _normalized_mixed_stream():
    return [
        TextDelta(text="before"),
        ToolCallDelta(index=0, id="c1", name="tool", arguments='{"x":1}'),
        TextDelta(text="after"),
        MessageStop(stop_reason=StopReason.TOOL_USE),
    ]


def _normalized_tool_only_stream():
    return [
        ToolCallDelta(index=0, id="c1", name="fn", arguments="{}"),
        MessageStop(stop_reason=StopReason.TOOL_USE),
    ]


class TestNonTextSkipped:
    @pytest.mark.parametrize(
        "stream",
        [
            pytest.param(_oai_tool_only_stream(), id="openai-tool-only"),
            pytest.param(_anthropic_tool_only_stream(), id="anthropic-tool-only"),
            pytest.param(_normalized_tool_only_stream(), id="normalized-tool-only"),
            pytest.param([MessageStop(stop_reason=StopReason.END_TURN)], id="normalized-stop-only"),
        ],
    )
    async def test_no_text_yields_nothing(self, stream):
        """Tool-call blocks and bare stop events must never produce text tokens."""
        tokens = [t async for t in stream_text(_agen(stream))]
        assert tokens == []

    @pytest.mark.parametrize(
        "stream",
        [
            pytest.param(_oai_mixed_stream(), id="openai"),
            pytest.param(_normalized_mixed_stream(), id="normalized"),
        ],
    )
    async def test_mixed_yields_only_text(self, stream):
        tokens = [t async for t in stream_text(_agen(stream))]
        assert tokens == ["before", "after"]

    async def test_stop_sentinel_not_a_token(self):
        """The finish_reason='stop' sentinel must not leak its value as a token."""
        tokens = [t async for t in stream_text(_agen(_oai_text_chunks("hi")))]
        assert "stop" not in tokens


# ---------------------------------------------------------------------------
# Anthropic context-manager style (has __aenter__, no __aiter__)
# ---------------------------------------------------------------------------


class TestAnthropicContextManager:
    async def test_yields_text(self):
        """Anthropic AsyncMessageStreamManager style: object with __aenter__ but no __aiter__."""
        manager = _FakeAnthropicStreamManager(_anthropic_text_events("Hello", " from", " Anthropic"))
        tokens = [t async for t in stream_text(manager)]
        assert tokens == ["Hello", " from", " Anthropic"]

    async def test_empty_yields_nothing(self):
        manager = _FakeAnthropicStreamManager([])
        tokens = [t async for t in stream_text(manager)]
        assert tokens == []


# ---------------------------------------------------------------------------
# Synchronous iterable inputs (passed without wrapping in _agen)
# ---------------------------------------------------------------------------


class TestSyncIterableInput:
    @pytest.mark.parametrize(
        "build",
        [
            pytest.param(_oai_text_chunks, id="openai"),
            pytest.param(_anthropic_text_events, id="anthropic"),
        ],
    )
    async def test_yields_text(self, build):
        tokens = [t async for t in stream_text(build("sync", " works"))]
        assert "".join(tokens) == "sync works"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    @pytest.mark.parametrize("bad", [42, None])
    async def test_unsupported_type_raises(self, bad):
        with pytest.raises(TypeError, match="unsupported provider_stream type"):
            async for _ in stream_text(bad):  # type: ignore[arg-type]
                pass


# ---------------------------------------------------------------------------
# Public export check
# ---------------------------------------------------------------------------


def test_exported_from_feishu_agent():
    import feishu.agent as agent

    assert hasattr(agent, "stream_text"), "stream_text must be exported from feishu.agent"
    assert agent.stream_text is stream_text
