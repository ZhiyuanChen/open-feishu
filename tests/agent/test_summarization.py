from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from feishu.agent.adapters.openai import _to_openai_messages
from feishu.agent.llm import Message, MessageStop, StopReason, TextDelta, TextPart, ToolResultPart, ToolUsePart
from feishu.agent.loop import AgentEngine
from feishu.agent.session import InMemorySessionStore
from feishu.agent.summarization import TextSummaryRequest, build_fast_text_summarizer, summarize_history
from feishu.agent.tools import ToolRegistry
from feishu.events.envelope import Event
from tests._fakes import FakeLlmBackend, text_turn, tool_turn


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


@pytest.mark.parametrize("keep_recent", (4, 5))
async def test_summary_keeps_the_entire_tool_exchange_at_the_cut(keep_recent: int) -> None:
    history = [
        Message(role="user", content=[TextPart(text="earlier request")]),
        Message(role="assistant", content=[TextPart(text="earlier reply")]),
        Message(role="user", content=[TextPart(text="use two tools")]),
        Message(
            role="assistant",
            content=[
                ToolUsePart(id="call_a", name="echo", arguments={"env": "a"}),
                ToolUsePart(id="call_b", name="echo", arguments={"env": "b"}),
            ],
        ),
        Message(role="tool", content=[ToolResultPart(tool_call_id="call_a", content="a")]),
        Message(role="tool", content=[ToolResultPart(tool_call_id="call_b", content="b")]),
        Message(role="assistant", content=[TextPart(text="tools complete")]),
        Message(role="user", content=[TextPart(text="later request")]),
        Message(role="assistant", content=[TextPart(text="later reply")]),
    ]
    summarized: list[Message] = []

    async def summarize(messages: list[Message]) -> str:
        summarized.extend(messages)
        return "earlier conversation"

    store = InMemorySessionStore()
    agent = AgentEngine(
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
        store=store,
        summarizer=summarize,
        summarize_keep_recent=keep_recent,
    )

    compacted = await summarize_history(agent, "summary_session", history)
    payload = _to_openai_messages(compacted, None)

    assert [message.role for message in summarized] == ["user", "assistant", "user"]
    assert [call["id"] for call in payload[1]["tool_calls"]] == ["call_a", "call_b"]
    assert [message["tool_call_id"] for message in payload if message["role"] == "tool"] == ["call_a", "call_b"]
    assert payload[-1] == {"role": "assistant", "content": "later reply"}
    assert await store.get("summary_session") == compacted


@pytest.mark.parametrize("automatic", (False, True), ids=("compact-command", "automatic-summary"))
async def test_agent_summary_sends_complete_tool_exchanges_to_the_model(automatic: bool) -> None:
    registry = ToolRegistry()

    async def echo(env: str) -> str:
        return env

    registry.register(
        "echo",
        echo,
        input_schema={"type": "object", "properties": {"env": {"type": "string"}}, "required": ["env"]},
        description="echo a value",
    )
    calls = tool_turn(id="auto_a", name="echo", arguments_json='{"env":"a"}')[:-1] + tool_turn(
        index=1, id="auto_b", name="echo", arguments_json='{"env":"b"}'
    )
    followups = 4 if automatic else 5
    backend = FakeLlmBackend([calls, text_turn("tools done"), *[text_turn("reply") for _ in range(followups + 1)]])

    async def summarize(_messages: list[Message]) -> str:
        return "earlier conversation"

    agent = AgentEngine(
        backend=backend,
        registry=registry,
        summarizer=summarize,
        summarize_threshold_tokens=74 if automatic else 0,
        compact_command=lambda text: text == "/compact",
    )

    def event(text: str, message_id: str) -> Event:
        return Event.from_payload(
            {
                "schema": "2.0",
                "header": {"event_type": "im.message.receive_v1", "event_id": f"event_{message_id}"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_summary"}, "sender_type": "user"},
                    "message": {
                        "chat_id": "oc_summary",
                        "message_id": message_id,
                        "message_type": "text",
                        "content": json.dumps({"text": text}),
                    },
                },
            }
        )

    await agent.run(event("two tools", "tools"))
    for index in range(followups):
        await agent.run(event("followup", f"followup_{index}"))
    if not automatic:
        await agent.run(event("/compact", "compact"))
    await agent.run(event("final followup", "final"))

    payload = _to_openai_messages(backend.calls[-1]["messages"], None)
    assert "earlier conversation" in payload[0]["content"]
    assert [call["id"] for message in payload for call in message.get("tool_calls", [])] == ["auto_a", "auto_b"]
    assert [message["tool_call_id"] for message in payload if message["role"] == "tool"] == ["auto_a", "auto_b"]
