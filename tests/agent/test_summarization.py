from __future__ import annotations

import asyncio
from typing import Any

from feishu.agent.llm import MessageStop, StopReason, TextDelta
from feishu.agent.summarization import TextSummaryRequest, build_fast_text_summarizer


class _TextBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)

        async def gen():
            yield TextDelta("摘要完成")
            yield MessageStop(stop_reason=StopReason.END_TURN)

        return gen()


class _EmptyBackend:
    def stream(self, **_kwargs):
        async def gen():
            yield MessageStop(stop_reason=StopReason.END_TURN)

        return gen()


def test_fast_text_summarizer_sends_text_only_prompt_to_fast_backend() -> None:
    backend = _TextBackend()
    summarizer = build_fast_text_summarizer(backend, timeout_seconds=1, default_max_chars=80)

    result = asyncio.run(
        summarizer(
            TextSummaryRequest(
                kind="mail",
                instruction="总结邮件。",
                text="Subject: Roadmap\nBody: Please review the roadmap.",
                max_chars=80,
            )
        )
    )

    assert result == "摘要完成"
    call = backend.calls[0]
    assert call["tools"] == ()
    assert "text" in call["messages"][0].content[0].text.lower()
    assert "Subject: Roadmap" in call["messages"][0].content[0].text


def test_fast_text_summarizer_returns_none_for_empty_output() -> None:
    summarizer = build_fast_text_summarizer(_EmptyBackend(), timeout_seconds=1, default_max_chars=80)

    result = asyncio.run(summarizer(TextSummaryRequest(kind="mail", instruction="总结邮件。", text="Subject: empty")))

    assert result is None
