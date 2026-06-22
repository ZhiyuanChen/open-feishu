import pytest

from feishu.agent.llm import (
    LlmBackend,
    Message,
    MessageStop,
    StopReason,
    TextDelta,
    TextPart,
    ToolCallDelta,
)
from tests._fakes import FakeLlmBackend, text_turn, tool_turn


def test_fake_is_llmbackend():
    assert isinstance(FakeLlmBackend([]), LlmBackend)


class TestStream:
    async def test_yields_scripted_chunks(self):
        fake = FakeLlmBackend([text_turn("hi")])
        chunks = [c async for c in fake.stream(messages=[], tools=())]
        assert chunks == [
            TextDelta(text="h"),
            TextDelta(text="i"),
            MessageStop(stop_reason=StopReason.END_TURN),
        ]

    async def test_records_call(self):
        fake = FakeLlmBackend([text_turn("hi")])
        [
            c
            async for c in fake.stream(
                messages=[Message(role="user", content=[TextPart(text="q")])],
                tools=(),
                system="sys",
            )
        ]
        assert len(fake.calls) == 1
        assert fake.calls[0]["system"] == "sys"
        assert isinstance(fake.calls[0]["messages"][0], Message)

    async def test_advances_each_call(self):
        fake = FakeLlmBackend([text_turn("a"), text_turn("b")])
        first = [c async for c in fake.stream(messages=[], tools=())]
        second = [c async for c in fake.stream(messages=[], tools=())]
        assert first[0] == TextDelta(text="a")
        assert second[0] == TextDelta(text="b")


class TestTurnHelpers:
    def test_tool_turn_splits_arguments(self):
        chunks = tool_turn(index=0, id="c1", name="weather", arguments_json='{"city":"sh"}')
        deltas = [c for c in chunks if isinstance(c, ToolCallDelta)]
        assert all(d.index == 0 for d in deltas)
        assert "".join(d.arguments for d in deltas) == '{"city":"sh"}'
        assert deltas[0].id == "c1" and deltas[0].name == "weather"
        assert isinstance(chunks[-1], MessageStop) and chunks[-1].stop_reason == StopReason.TOOL_USE


class TestExhaustion:
    @pytest.mark.parametrize("repeat_last", [False, None])
    async def test_over_request_raises(self, repeat_last):
        """Requesting more turns than scripted fails loudly (default and explicit repeat_last=False)."""
        kwargs = {} if repeat_last is None else {"repeat_last": repeat_last}
        fake = FakeLlmBackend([text_turn("only")], **kwargs)
        [c async for c in fake.stream(messages=[], tools=())]
        with pytest.raises(AssertionError, match=r"no more scripted turns.*call #2"):
            [c async for c in fake.stream(messages=[], tools=())]

    async def test_repeat_last_replays_final_turn(self):
        """With repeat_last=True the last scripted turn replays on every extra call."""
        turn = tool_turn(index=0, id="t1", name="fn", arguments_json='{"x":1}')
        fake = FakeLlmBackend([turn], repeat_last=True)
        expected_deltas = [c for c in turn if isinstance(c, ToolCallDelta)]
        for _ in range(3):
            chunks = [c async for c in fake.stream(messages=[], tools=())]
            deltas = [c for c in chunks if isinstance(c, ToolCallDelta)]
            assert deltas == expected_deltas
            stop = chunks[-1]
            assert isinstance(stop, MessageStop) and stop.stop_reason == StopReason.TOOL_USE
        assert len(fake.calls) == 3
