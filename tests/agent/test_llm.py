import pytest

from feishu.agent.llm import (
    LlmBackend,
    Message,
    MessageStop,
    StopReason,
    TextDelta,
    TextPart,
    ToolCall,
    ToolCallDelta,
    ToolResultPart,
    ToolSpec,
    ToolUsePart,
)
from feishu.agent.loop import accumulate_stream


class TestContentParts:
    """Public content-part dataclasses carry the values callers read."""

    def test_text_part_exposes_text(self):
        assert TextPart(text="hi").text == "hi"

    def test_tool_use_fields(self):
        u = ToolUsePart(id="call_1", name="lookup", arguments={"q": "x"})
        assert u.id == "call_1" and u.name == "lookup" and u.arguments == {"q": "x"}

    def test_tool_result_not_error_default(self):
        r = ToolResultPart(tool_call_id="call_1", content="42")
        assert r.tool_call_id == "call_1" and r.content == "42" and r.is_error is False


def test_message_and_toolspec_fields():
    m = Message(role="user", content=[TextPart(text="hello")])
    assert m.role == "user" and isinstance(m.content[0], TextPart)
    spec = ToolSpec(name="lookup", description="look up", input_schema={"type": "object"})
    assert spec.name == "lookup" and spec.input_schema == {"type": "object"}


# Adapters map provider strings to these and may serialize them, so the literals are wire facts.
@pytest.mark.parametrize(
    "member, value",
    [
        (StopReason.END_TURN, "end_turn"),
        (StopReason.TOOL_USE, "tool_use"),
        (StopReason.MAX_TOKENS, "max_tokens"),
        (StopReason.REFUSAL, "refusal"),
        (StopReason.OTHER, "other"),
    ],
)
def test_stop_reason_values(member, value):
    assert member == value
    assert StopReason(value) is member


def test_stream_chunk_dataclasses_construct():
    assert TextDelta(text="ab").text == "ab"
    d = ToolCallDelta(index=0)
    assert d.index == 0 and d.id is None and d.name is None and d.arguments == ""
    stop = MessageStop(stop_reason=StopReason.TOOL_USE)
    assert stop.stop_reason is StopReason.TOOL_USE and stop.usage is None
    call = ToolCall(id="c1", name="lookup", arguments='{"q":"x"}')
    assert call.arguments == '{"q":"x"}'


async def test_accumulate_stream_handles_all_chunks():
    # Behavioral coverage that the public consumer handles each StreamChunk variant.
    async def chunks():
        yield TextDelta(text="hi")
        yield ToolCallDelta(index=0, id="c1", name="fn", arguments="{}")
        yield MessageStop(stop_reason=StopReason.TOOL_USE)

    result = await accumulate_stream(chunks())
    assert result.text == "hi"
    assert result.tool_calls == [ToolCall(id="c1", name="fn", arguments="{}")]
    assert result.stop_reason == StopReason.TOOL_USE


def test_llmbackend_is_runtime_checkable():
    # The @runtime_checkable Protocol is the public extension contract for custom backends.
    class Impl:
        def stream(self, *, messages, tools=(), system=None, **kwargs):
            async def gen():
                yield TextDelta(text="x")

            return gen()

    assert isinstance(Impl(), LlmBackend)

    class NotImpl:
        pass

    assert not isinstance(NotImpl(), LlmBackend)
