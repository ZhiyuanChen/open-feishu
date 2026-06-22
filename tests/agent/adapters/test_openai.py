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

import json
from types import SimpleNamespace

import pytest

from feishu.agent.adapters.openai import OpenAIBackend
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


def _chunk(*, content=None, tool_calls=None, finish_reason=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _tc(index, *, id=None, name=None, arguments=None):
    func = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=id, function=func)


class _FakeOpenAIClient:
    """Stand-in for openai.AsyncOpenAI.

    Records the params passed to ``chat.completions.create(...)`` and replays a
    scripted list of chunk objects as the async-iterable stream the SDK returns.
    """

    def __init__(self, chunks):
        self._chunks = chunks
        self.last_params: dict | None = None
        self.chat = SimpleNamespace(completions=self._Completions(self))

    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        async def create(self, **params):
            self._outer.last_params = params
            chunks = self._outer._chunks

            async def _gen():
                for c in chunks:
                    yield c

            return _gen()


async def _backend_chunks(chunks, *, messages=None, tools=(), system=None):
    backend = OpenAIBackend(client=_FakeOpenAIClient(chunks), model="gpt-4o")
    msgs = messages if messages is not None else [Message(role="user", content=[TextPart(text="q")])]
    return [c async for c in backend.stream(messages=msgs, tools=tools, system=system)]


class TestStreamTranslation:
    """Provider chunks surface as neutral StreamChunks through the public stream()."""

    async def test_text_then_stop(self):
        chunks = [
            _chunk(content="Hel"),  # codespell:ignore
            _chunk(content="lo"),
            _chunk(finish_reason="stop"),
            _chunk(usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2)),
        ]
        out = await _backend_chunks(chunks)
        assert out[0] == TextDelta(text="Hel")  # codespell:ignore
        assert out[1] == TextDelta(text="lo")
        assert out[-1] == MessageStop(
            stop_reason=StopReason.END_TURN, usage={"prompt_tokens": 4, "completion_tokens": 2}
        )

    async def test_fragmented_tool_call(self):
        chunks = [
            _chunk(tool_calls=[_tc(0, id="call_1", name="weather", arguments='{"ci')]),
            _chunk(tool_calls=[_tc(0, arguments='ty":"sh"}')]),
            _chunk(finish_reason="tool_calls"),
        ]
        out = await _backend_chunks(chunks)
        tool_deltas = [c for c in out if isinstance(c, ToolCallDelta)]
        assert tool_deltas[0] == ToolCallDelta(index=0, id="call_1", name="weather", arguments='{"ci')
        # fragmented arguments are forwarded verbatim for the accumulator to reassemble
        assert "".join(d.arguments for d in tool_deltas) == '{"city":"sh"}'
        assert out[-1].stop_reason == StopReason.TOOL_USE

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("stop", StopReason.END_TURN),
            ("tool_calls", StopReason.TOOL_USE),
            ("length", StopReason.MAX_TOKENS),
            ("content_filter", StopReason.REFUSAL),
            ("something_else", StopReason.OTHER),  # unknown provider reason -> OTHER
        ],
    )
    async def test_finish_reason_maps(self, raw, expected):
        out = await _backend_chunks([_chunk(content="x"), _chunk(finish_reason=raw)])
        assert out[-1] == MessageStop(stop_reason=expected, usage=None)


class TestRequestPayload:
    """The wire request the backend builds is the provider contract worth pinning."""

    async def test_tools_and_messages(self):
        client = _FakeOpenAIClient([_chunk(finish_reason="stop")])
        backend = OpenAIBackend(client=client, model="gpt-4o")
        specs = [ToolSpec(name="weather", description="d", input_schema={"type": "object"})]
        msgs = [
            Message(role="user", content=[TextPart(text="hi")]),
            Message(role="assistant", content=[ToolUsePart(id="t1", name="weather", arguments={"city": "sh"})]),
            Message(role="tool", content=[ToolResultPart(tool_call_id="t1", content="sunny")]),
        ]
        [c async for c in backend.stream(messages=msgs, tools=specs, system="be nice")]

        params = client.last_params
        assert params["model"] == "gpt-4o"
        tool0 = params["tools"][0]
        assert tool0["type"] == "function"  # required OpenAI tool-envelope wire field
        fn = tool0["function"]
        assert (fn["name"], fn["description"], fn["parameters"]) == ("weather", "d", {"type": "object"})

        system, user, assistant, tool = params["messages"]
        assert system == {"role": "system", "content": "be nice"}
        assert user == {"role": "user", "content": "hi"}
        tc = assistant["tool_calls"][0]
        assert tc["id"] == "t1" and tc["type"] == "function" and tc["function"]["name"] == "weather"
        assert json.loads(tc["function"]["arguments"]) == {"city": "sh"}
        # provider wire fact: a tool result is sent as a tool-role message
        assert tool == {"role": "tool", "tool_call_id": "t1", "content": "sunny"}


def test_satisfies_protocol():
    pytest.importorskip("openai")
    backend = OpenAIBackend(client=object(), model="gpt-4o")
    assert isinstance(backend, LlmBackend)
