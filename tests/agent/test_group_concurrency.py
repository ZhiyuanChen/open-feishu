from __future__ import annotations

import asyncio
import gc
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from feishu.agent.context import current_tool_context
from feishu.agent.llm import Message, MessageStop, StopReason, TextDelta, TextPart
from feishu.agent.loop import AgentEngine, session_id_for
from feishu.agent.session import InMemorySessionStore
from feishu.agent.tools import ToolRegistry
from feishu.events.conversations import is_group_message, session_id_for_message
from feishu.events.envelope import Event
from feishu.events.receiver import _default_partition_key


def message_event(
    text: str,
    *,
    chat_type: str | None = "group",
    message_id: str | None = None,
    root_id: str | None = None,
    user_id: str = "ou_a",
    event_id: str | None = None,
) -> Event:
    message: dict[str, Any] = {
        "chat_id": "oc_chat",
        "message_id": message_id or f"om_{text}",
        "message_type": "text",
        "content": json.dumps({"text": text}),
    }
    if chat_type is not None:
        message["chat_type"] = chat_type
    if root_id is not None:
        message["root_id"] = root_id
    return Event.from_payload(
        {
            "header": {"event_type": "im.message.receive_v1", "event_id": event_id or f"ev_{text}"},
            "event": {"message": message, "sender": {"sender_type": "user", "sender_id": {"open_id": user_id}}},
        }
    )


def text_of(message: Message) -> str:
    return "".join(part.text for part in message.content if isinstance(part, TextPart))


class ControlledBackend:
    def __init__(self) -> None:
        self.started = {name: asyncio.Event() for name in ("first", "second")}
        self.release = {name: asyncio.Event() for name in ("first", "second")}
        self.cancelled: set[str] = set()
        self.histories: dict[str, list[Message]] = {}

    async def stream(self, *, messages: Sequence[Message], **kwargs: Any) -> AsyncIterator[Any]:
        name = text_of(next(message for message in reversed(messages) if message.role == "user"))
        self.histories[name] = list(messages)
        self.started[name].set()
        try:
            await self.release[name].wait()
        except asyncio.CancelledError:
            self.cancelled.add(name)
            raise
        yield TextDelta(text=f"answer {name}")
        yield MessageStop(stop_reason=StopReason.END_TURN)


async def cancel_tasks(*tasks: asyncio.Task[Any]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("second_user", ["ou_a", "ou_b"])
async def test_independent_group_questions_run_together_with_isolated_histories(second_user: str) -> None:
    backend = ControlledBackend()
    store = InMemorySessionStore()
    agent = AgentEngine(backend=backend, registry=ToolRegistry(), store=store)
    first = asyncio.create_task(agent.run(message_event("first")))
    second: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(backend.started["first"].wait(), 1)
        second = asyncio.create_task(agent.run(message_event("second", user_id=second_user)))
        await asyncio.wait_for(backend.started["second"].wait(), 1)
        assert not first.done()
        assert backend.cancelled == set()
        assert [text_of(message) for message in backend.histories["second"]] == ["second"]
        backend.release["first"].set()
        backend.release["second"].set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert [text_of(message) for message in await store.get("oc_chat:om_first")] == ["first", "answer first"]
        assert [text_of(message) for message in await store.get("oc_chat:om_second")] == ["second", "answer second"]
    finally:
        await cancel_tasks(first, *([second] if second is not None else []))


async def test_group_topic_continuations_wait_for_the_complete_previous_turn() -> None:
    backend = ControlledBackend()
    store = InMemorySessionStore()
    agent = AgentEngine(backend=backend, registry=ToolRegistry(), store=store)
    first = asyncio.create_task(agent.run(message_event("first")))
    second: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(backend.started["first"].wait(), 1)
        second = asyncio.create_task(agent.run(message_event("second", root_id="om_first", user_id="ou_b")))
        await asyncio.sleep(0)
        assert not backend.started["second"].is_set()
        assert backend.cancelled == set()
        backend.release["first"].set()
        await asyncio.wait_for(backend.started["second"].wait(), 1)
        assert [text_of(message) for message in backend.histories["second"]] == ["first", "answer first", "second"]
        backend.release["second"].set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert [message.role for message in await store.get("oc_chat:om_first")] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
    finally:
        await cancel_tasks(first, *([second] if second is not None else []))


async def test_private_followup_interrupts_even_when_it_replies_to_an_older_message() -> None:
    backend = ControlledBackend()
    agent = AgentEngine(backend=backend, registry=ToolRegistry())
    first = asyncio.create_task(agent.run(message_event("first", chat_type="p2p")))
    second: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(backend.started["first"].wait(), 1)
        second = asyncio.create_task(agent.run(message_event("second", chat_type="p2p", root_id="om_first")))
        await asyncio.wait_for(backend.started["second"].wait(), 1)
        backend.release["second"].set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert backend.cancelled == {"first"}
        assert [text_of(message) for message in backend.histories["second"]] == ["first", "second"]
    finally:
        await cancel_tasks(first, *([second] if second is not None else []))


async def test_cancelling_queued_group_turn_does_not_hide_running_work_from_shutdown() -> None:
    backend = ControlledBackend()
    agent = AgentEngine(backend=backend, registry=ToolRegistry())
    first = asyncio.create_task(agent.run(message_event("first", root_id="om_root")))
    second: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(backend.started["first"].wait(), 1)
        second = asyncio.create_task(agent.run(message_event("second", root_id="om_root")))
        await asyncio.sleep(0)
        second.cancel()
        await asyncio.gather(second, return_exceptions=True)
        assert not first.done()
        assert not backend.started["second"].is_set()
        await agent.aclose(deadline=asyncio.get_running_loop().time())
        await asyncio.wait_for(asyncio.gather(first, return_exceptions=True), 1)
        assert backend.cancelled == {"first"}
    finally:
        await cancel_tasks(first, *([second] if second is not None else []))


async def test_shutdown_cancels_running_and_waiting_group_turns() -> None:
    backend = ControlledBackend()
    agent = AgentEngine(backend=backend, registry=ToolRegistry())
    first = asyncio.create_task(agent.run(message_event("first", root_id="om_root")))
    second: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(backend.started["first"].wait(), 1)
        second = asyncio.create_task(agent.run(message_event("second", root_id="om_root")))
        await asyncio.sleep(0)
        await agent.aclose(deadline=asyncio.get_running_loop().time())
        await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)
        assert backend.cancelled == {"first"}
        assert not backend.started["second"].is_set()
    finally:
        await cancel_tasks(first, *([second] if second is not None else []))


async def test_parallel_group_turns_keep_each_requesting_user_in_its_tool_context() -> None:
    from tests._fakes import text_turn, tool_turn

    class ToolBackend:
        async def stream(self, *, messages: Sequence[Message], **kwargs: Any) -> AsyncIterator[Any]:
            if messages[-1].role == "user":
                name = text_of(messages[-1])
                chunks = tool_turn(index=0, id=f"call_{name}", name="who", arguments_json="{}")
            else:
                chunks = text_turn("done")
            for chunk in chunks:
                yield chunk

    started = {user: asyncio.Event() for user in ("ou_a", "ou_b")}
    release = asyncio.Event()
    observed_users: list[str] = []

    async def who() -> str:
        user = current_tool_context().requesting_user()["open_id"]
        started[user].set()
        await release.wait()
        observed_users.append(current_tool_context().requesting_user()["open_id"])
        return user

    registry = ToolRegistry()
    registry.register("who", who, input_schema={"type": "object", "properties": {}}, description="requesting user")
    store = InMemorySessionStore()
    agent = AgentEngine(backend=ToolBackend(), registry=registry, store=store)
    first = asyncio.create_task(agent.run(message_event("first")))
    second = asyncio.create_task(agent.run(message_event("second", user_id="ou_b")))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 1)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert sorted(observed_users) == ["ou_a", "ou_b"]
        for session_id in ("oc_chat:om_first", "oc_chat:om_second"):
            assert [message.role for message in await store.get(session_id)] == [
                "user",
                "assistant",
                "tool",
                "assistant",
            ]
    finally:
        await cancel_tasks(first, second)


async def test_completed_group_roots_do_not_retain_one_session_lock_each() -> None:
    backend = ControlledBackend()
    backend.release["first"].set()
    agent = AgentEngine(backend=backend, registry=ToolRegistry())
    for index in range(20):
        await agent.run(message_event("first", message_id=f"om_root_{index}", event_id=f"ev_{index}"))
    gc.collect()
    assert len(agent._session_locks) == 0


@pytest.mark.parametrize(
    ("chat_type", "root_id", "expected"),
    [
        ("p2p", None, "oc_chat"),
        ("p2p", "om_root", "oc_chat"),
        ("group", None, "oc_chat:om_first"),
        ("group", "om_root", "oc_chat:om_root"),
        ("topic_group", None, "oc_chat:om_first"),
        (None, None, "oc_chat"),
        (None, "om_root", "oc_chat:om_root"),
    ],
)
def test_conversation_and_durable_partition_use_the_same_chat_policy(
    chat_type: str | None, root_id: str | None, expected: str
) -> None:
    event = message_event("first", chat_type=chat_type, root_id=root_id)
    assert session_id_for(event) == expected
    assert session_id_for_message(event) == expected
    assert _default_partition_key(event) == expected


def test_group_missing_message_identity_uses_a_unique_stable_event_lane() -> None:
    event = message_event("first")
    del event.body["message"]["message_id"]
    assert session_id_for(event) == "oc_chat:event:ev_first"
    assert _default_partition_key(event) == "oc_chat:event:ev_first"


def test_group_without_any_branch_identity_is_rejected_before_creating_shared_history() -> None:
    event = Event.from_payload({"header": {}, "event": {"message": {"chat_type": "group", "chat_id": "oc_chat"}}})
    with pytest.raises(ValueError, match="identity"):
        session_id_for(event)


@pytest.mark.parametrize(
    ("chat_type", "expected"),
    [("group", True), (" GROUP ", True), ("topic_group", True), ("p2p", False), (None, False)],
)
def test_group_classification_uses_explicit_chat_type(chat_type: str | None, expected: bool) -> None:
    assert is_group_message(message_event("first", chat_type=chat_type)) is expected
