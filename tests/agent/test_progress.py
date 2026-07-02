from __future__ import annotations

import asyncio

from feishu.agent.llm import MessageStop, ReasoningDelta, StopReason
from feishu.agent.progress import ProgressSnapshot, _clean_status, _prompt, build_progress_summarizer


class _EmptyBackend:
    def stream(self, **_kwargs):
        async def gen():
            yield MessageStop(stop_reason=StopReason.END_TURN)

        return gen()


class _ReasoningBackend:
    def stream(self, **_kwargs):
        async def gen():
            yield ReasoningDelta("正在根据当前时间和时区判断日期")
            yield MessageStop(stop_reason=StopReason.END_TURN)

        return gen()


def test_progress_prompt_uses_natural_context_without_elapsed_seconds() -> None:
    prompt = _prompt(
        ProgressSnapshot(
            phase="thinking",
            elapsed_seconds=12.3,
            reasoning="用户想查看明天上午的日程，正在结合当前时间和时区判断日期。",
        ),
        max_chars=60,
    )

    assert "进展信息：" in prompt
    assert "已耗时" not in prompt
    assert "12.3" not in prompt
    assert "raw_reasoning_tail" not in prompt


def test_progress_summarizer_has_safe_empty_response_fallback() -> None:
    summarizer = build_progress_summarizer(_EmptyBackend(), timeout_seconds=1, max_chars=60)
    status = asyncio.run(
        summarizer(
            ProgressSnapshot(
                phase="tool",
                tool_name="create_calendar_event",
                tool_description="创建日程",
            )
        )
    )

    assert status == "创建日程"


def test_progress_status_strips_elapsed_time_phrasing() -> None:
    assert _clean_status("已经处理了 12.3 秒，正在整理日程。", max_chars=60) == "正在整理日程。"


def test_progress_summarizer_accepts_fast_model_reasoning_channel() -> None:
    summarizer = build_progress_summarizer(_ReasoningBackend(), timeout_seconds=1, max_chars=60)
    status = asyncio.run(summarizer(ProgressSnapshot(phase="thinking")))

    assert status == "正在根据当前时间和时区判断日期"
