from __future__ import annotations

import json

from feishu.agent.llm import Message, MessageStop, StopReason, TextDelta, TextPart, ToolResultPart, ToolUsePart
from feishu.agent.loop import AgentEngine
from feishu.agent.persistence import message_from_dict, message_to_dict
from feishu.agent.session import InMemorySessionStore
from feishu.agent.tools import ToolRegistry
from feishu.events.envelope import Event
from feishu.events.job_runner import EventJobRunner
from feishu.events.jobs import EventJobStatus, InMemoryEventJobStore
from tests._fakes import FakeLlmBackend, text_turn, tool_turn


def _event(event_id: str) -> Event:
    return Event.from_payload(
        {
            "schema": "2.0",
            "header": {"event_id": event_id, "event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_user"}, "sender_type": "user"},
                "message": {
                    "chat_id": "oc_chat",
                    "message_id": f"om_{event_id}",
                    "message_type": "text",
                    "content": json.dumps({"text": "hello"}),
                },
            },
        }
    )


class _RetryOnceBackend:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("temporary model timeout")

        async def response():
            yield TextDelta(text="reply")
            yield MessageStop(stop_reason=StopReason.END_TURN)

        return response()


def test_message_source_event_id_round_trips_and_old_data_stays_valid() -> None:
    message = Message(role="user", content=[TextPart(text="hello")], source_event_id="evt-1")

    restored = message_from_dict(message_to_dict(message))
    old = message_from_dict({"role": "user", "content": [{"k": "text", "text": "old"}]})

    assert restored.source_event_id == "evt-1"
    assert old.source_event_id is None


async def test_pre_effect_retry_does_not_duplicate_inbound_user_message() -> None:
    clock = [5.0]
    jobs = InMemoryEventJobStore()
    event = _event("evt-1")
    assert await jobs.enqueue(event.event_id, event.raw, partition_key="oc_chat", now=1)
    session_store = InMemorySessionStore()
    backend = _RetryOnceBackend()
    engine = AgentEngine(backend=backend, registry=ToolRegistry(), store=session_store, stream=False)
    runner = EventJobRunner(
        jobs,
        engine.run,
        worker_id="worker-a",
        now=lambda: clock[0],
        backoff_base_seconds=1,
    )

    first = await runner.run_once()
    clock[0] = 6.0
    second = await runner.run_once()

    history = await session_store.get("oc_chat")
    assert first is not None and first.status is EventJobStatus.RETRY_WAIT
    assert second is not None and second.status is EventJobStatus.COMPLETED
    assert backend.calls == 2
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].source_event_id == "evt-1"
    reply = history[1].content[0]
    assert isinstance(reply, TextPart)
    assert reply.text == "reply"


async def test_next_turn_closes_an_unresolved_tool_call_as_unknown() -> None:
    history_store = InMemorySessionStore()
    await history_store.set(
        "oc_chat",
        [Message(role="assistant", content=[ToolUsePart(id="call-1", name="write", arguments={"x": 1})])],
    )
    backend = FakeLlmBackend([text_turn("continued")])
    engine = AgentEngine(backend=backend, registry=ToolRegistry(), store=history_store, stream=False)

    await engine.run(_event("evt-next"))

    messages = backend.calls[0]["messages"]
    recovered = [part for message in messages for part in message.content if isinstance(part, ToolResultPart)]
    assert len(recovered) == 1
    assert recovered[0].tool_call_id == "call-1"
    assert recovered[0].is_error is True
    assert "outcome is unknown" in recovered[0].content


async def test_recovery_does_not_reuse_tool_ids_across_assistant_batches() -> None:
    history_store = InMemorySessionStore()
    await history_store.set(
        "oc_chat",
        [
            Message(role="assistant", content=[ToolUsePart(id="reused", name="read", arguments={})]),
            Message(role="tool", content=[ToolResultPart(tool_call_id="reused", content="old result")]),
            Message(role="assistant", content=[ToolUsePart(id="reused", name="write", arguments={})]),
        ],
    )
    backend = FakeLlmBackend([text_turn("continued")])
    engine = AgentEngine(backend=backend, registry=ToolRegistry(), store=history_store, stream=False)

    await engine.run(_event("evt-next"))

    messages = backend.calls[0]["messages"]
    results = [part for message in messages if message.role == "tool" for part in message.content]
    assert len(results) == 2
    assert results[0].content == "old result"
    assert results[1].content != "old result"
    assert results[1].is_error is True


async def test_tool_exception_after_dispatch_freezes_durable_job() -> None:
    jobs = InMemoryEventJobStore()
    event = _event("evt-tool")
    await jobs.enqueue(event.event_id, event.raw, partition_key="oc_chat", now=1)
    calls = []

    async def write_then_timeout(**_arguments):
        calls.append("write")
        raise TimeoutError("remote result unknown")

    registry = ToolRegistry()
    registry.register(
        "write",
        write_then_timeout,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        description="writes remotely",
    )
    backend = FakeLlmBackend(
        [
            tool_turn(id="call-1", name="write", arguments_json="{}"),
            text_turn("the call finished"),
        ]
    )
    engine = AgentEngine(backend=backend, registry=registry, store=InMemorySessionStore(), stream=False)
    runner = EventJobRunner(jobs, engine.run, worker_id="worker-a", now=lambda: 5)

    result = await runner.run_once()

    assert result is not None and result.status is EventJobStatus.UNKNOWN
    assert calls == ["write"]
