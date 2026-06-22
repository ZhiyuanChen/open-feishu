from __future__ import annotations

from typing import Any, Sequence

from feishu.agent.llm import (
    Message,
    MessageStop,
    StopReason,
    StreamChunk,
    TextDelta,
    ToolCallDelta,
    ToolSpec,
)


class FakeLlmBackend:
    """Test double implementing LlmBackend.stream by replaying scripted chunk lists.

    By default, exhausting all scripted turns raises AssertionError so unexpected
    extra LLM calls fail loudly.  Pass ``repeat_last=True`` to instead repeat the
    last scripted turn indefinitely — useful for max_iterations tests where the
    agent loop itself is expected to be the bounding condition.
    """

    def __init__(self, scripts: list[list[StreamChunk]], *, repeat_last: bool = False):
        self._scripts = list(scripts)
        self._repeat_last = repeat_last
        self._last_script: list[StreamChunk] | None = None
        self.calls: list[dict] = []

    def stream(
        self,
        *,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        system: str | None = None,
        **kwargs: Any,
    ):
        call_n = len(self.calls) + 1
        self.calls.append({"messages": list(messages), "tools": list(tools), "system": system, "kwargs": kwargs})
        if self._scripts:
            self._last_script = self._scripts.pop(0)
            script = self._last_script
        elif self._repeat_last and self._last_script is not None:
            script = self._last_script
        else:
            raise AssertionError(f"FakeLlmBackend: no more scripted turns (over-request on call #{call_n})")

        async def _gen():
            for chunk in script:
                yield chunk

        return _gen()


def text_turn(text: str, *, stop: StopReason = StopReason.END_TURN) -> list[StreamChunk]:
    chunks: list[StreamChunk] = [TextDelta(text=ch) for ch in text]
    chunks.append(MessageStop(stop_reason=stop))
    return chunks


def tool_turn(*, index: int = 0, id: str, name: str, arguments_json: str) -> list[StreamChunk]:
    mid = max(1, len(arguments_json) // 2)
    return [
        ToolCallDelta(index=index, id=id, name=name, arguments=arguments_json[:mid]),
        ToolCallDelta(index=index, arguments=arguments_json[mid:]),
        MessageStop(stop_reason=StopReason.TOOL_USE),
    ]
