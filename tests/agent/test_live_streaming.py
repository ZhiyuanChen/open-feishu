from __future__ import annotations

import asyncio
import json

import pytest

from feishu.agent.llm import MessageStop, ReasoningDelta, StopReason, TextDelta, TextPart
from feishu.agent.loop import AgentEngine
from feishu.agent.session import InMemorySessionStore
from feishu.agent.tools import ToolRegistry
from feishu.events.envelope import Event
from tests.conftest import envelope


def _event() -> Event:
    return Event.from_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1", "event_id": "stream-event"},
            "event": {
                "sender": {"sender_type": "user"},
                "message": {
                    "chat_id": "oc_stream",
                    "message_id": "om_in",
                    "message_type": "text",
                    "content": '{"text":"hello"}',
                },
            },
        }
    )


@pytest.mark.parametrize("cancel", [False, True])
async def test_streaming_writes_first_text_before_model_finishes(client_factory, recorder, cancel):
    """Buffering the whole model turn must not delay the first visible card update."""
    visible = asyncio.Event()
    resume = asyncio.Event()
    model_closed = asyncio.Event()

    class Backend:
        def stream(self, **_kwargs):
            async def chunks():
                try:
                    yield ReasoningDelta(text="private reasoning")
                    yield TextDelta(text="Hello")
                    await resume.wait()
                    yield TextDelta(text=" world")
                    yield MessageStop(stop_reason=StopReason.END_TURN)
                finally:
                    model_closed.set()

            return chunks()

    def respond(request):
        if request.method == "PUT":
            visible.set()
        return envelope({"card_id": "card_live", "message_id": "om_reply"})

    client = client_factory(recorder=recorder, responder=respond)
    store = InMemorySessionStore()
    agent = AgentEngine(backend=Backend(), registry=ToolRegistry(), store=store, client=client, stream=True)
    turn = asyncio.create_task(agent.run(_event()))
    try:
        await asyncio.wait_for(visible.wait(), timeout=1)
        assert not turn.done()
        writes = [body["content"] for method, _path, _params, body in recorder if method == "PUT"]
        assert writes == ["Hello"]
        if cancel:
            turn.cancel()
            with pytest.raises(asyncio.CancelledError):
                await turn
        else:
            resume.set()
            await asyncio.wait_for(turn, timeout=1)
            writes = [body["content"] for method, _path, _params, body in recorder if method == "PUT"]
            assert writes[-1] == "Hello world"
        assert model_closed.is_set()
        settings = [body for method, path, _params, body in recorder if path.endswith("/settings")]
        assert len(settings) == 1
        assert json.loads(settings[0]["settings"])["config"]["streaming_mode"] is False
        replies = [body for method, path, _params, body in recorder if path.endswith("/reply")]
        assert len(replies) == 1
        assert replies[0]["msg_type"] == "interactive"
        history = await store.get("oc_stream")
        answers = [
            part.text
            for message in history
            if message.role == "assistant"
            for part in message.content
            if isinstance(part, TextPart)
        ]
        assert answers == ([] if cancel else ["Hello world"])
    finally:
        resume.set()
        if not turn.done():
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
        await client.aclose()


@pytest.mark.parametrize("cancel", [False, True])
async def test_source_close_failure_preserves_model_error_or_cancellation(client_factory, cancel):
    visible = asyncio.Event()
    resume = asyncio.Event()

    class Source:
        def __init__(self):
            self.started = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.started:
                self.started = True
                return TextDelta(text="Hello")
            await resume.wait()
            raise RuntimeError("model failed")

        async def aclose(self):
            raise ValueError("source close failed")

    class Backend:
        def stream(self, **_kwargs):
            return Source()

    def respond(request):
        if request.method == "PUT":
            visible.set()
        return envelope({"card_id": "card_live", "message_id": "om_reply"})

    client = client_factory(responder=respond)
    agent = AgentEngine(backend=Backend(), registry=ToolRegistry(), client=client, stream=True)
    turn = asyncio.create_task(agent.run(_event()))
    try:
        await asyncio.wait_for(visible.wait(), timeout=1)
        if cancel:
            turn.cancel()
            with pytest.raises(asyncio.CancelledError):
                await turn
        else:
            resume.set()
            with pytest.raises(RuntimeError, match="model failed"):
                await turn
    finally:
        if not turn.done():
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
        await client.aclose()
