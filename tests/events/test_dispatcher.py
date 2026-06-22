import pytest

from feishu.events.dispatcher import EventDispatcher
from feishu.events.envelope import Event
from feishu.events.idempotency import InMemorySeenStore


def _message_event(event_id="evt_1"):
    return Event.from_payload(
        {"schema": "2.0", "header": {"event_type": "im.message.receive_v1", "event_id": event_id}, "event": {}}
    )


def _card_event(event_id="c1"):
    return Event.from_payload(
        {"schema": "2.0", "header": {"event_type": "card.action.trigger", "event_id": event_id}, "event": {}}
    )


@pytest.fixture
def dispatcher():
    return EventDispatcher()


class TestRouting:
    async def test_routes_to_matching_handler(self, dispatcher):
        d = dispatcher
        seen = []

        @d.on("im.message.receive_v1")
        async def handle(event):
            seen.append(event.event_id)

        result = await d.dispatch(_message_event("evt_42"))
        assert seen == ["evt_42"]
        assert result is None

    @pytest.mark.parametrize(
        "registered, expected",
        [
            (["*"], {"star"}),  # star fallback runs when no exact match
            (["im.message.receive_v1", "*"], {"exact", "star"}),  # both run
        ],
    )
    async def test_exact_and_star_handlers(self, dispatcher, registered, expected):
        d = dispatcher
        ran = set()
        labels = {"im.message.receive_v1": "exact", "*": "star"}
        for event_type in registered:
            label = labels[event_type]

            @d.on(event_type)
            async def handler(event, _label=label):
                ran.add(_label)

        await d.dispatch(_message_event())
        assert ran == expected

    async def test_no_handlers_returns_none(self, dispatcher):
        assert await dispatcher.dispatch(_message_event()) is None

    async def test_card_return_passes_through(self, dispatcher):
        d = dispatcher
        reply = {"toast": {"type": "success", "content": "ok"}, "card": {"schema": "2.0"}}

        @d.on("card.action.trigger")
        async def on_card(event):
            return reply

        assert await d.dispatch(_card_event()) == reply


class TestDedup:
    @pytest.mark.parametrize("event_id", ["dup", ""])
    async def test_runs_once(self, event_id):
        """Duplicate event_id (including the empty string) must not bypass dedup."""
        d = EventDispatcher(seen_store=InMemorySeenStore())
        calls = []

        @d.on("im.message.receive_v1")
        async def handle(event):
            calls.append(event.event_id)

        await d.dispatch(_message_event(event_id))
        await d.dispatch(_message_event(event_id))  # duplicate -> skipped
        assert calls == [event_id]


class TestErrorIsolation:
    async def test_isolates_failure(self, dispatcher):
        d = dispatcher
        ran = []

        @d.on("im.message.receive_v1")
        async def boom(event):
            raise RuntimeError("handler exploded")

        @d.on("im.message.receive_v1")
        async def after(event):
            ran.append("after")

        @d.on("*")
        async def fallback(event):
            ran.append("fallback")

        # dispatch must not raise, and both the later exact handler and the fallback still run.
        result = await d.dispatch(_message_event())
        assert ran == ["after", "fallback"]
        assert result is None

    async def test_on_error_gets_exc_event(self, dispatcher):
        d = dispatcher
        seen = {}

        @d.on("im.message.receive_v1")
        async def boom(event):
            raise ValueError("nope")

        @d.on_error
        async def report(exc, event):
            seen["exc"] = exc
            seen["event_id"] = event.event_id

        await d.dispatch(_message_event("evt_err"))
        assert isinstance(seen["exc"], ValueError)
        assert seen["event_id"] == "evt_err"

    async def test_on_error_return_becomes_result(self, dispatcher):
        d = dispatcher

        @d.on("card.action.trigger")
        async def boom(event):
            raise RuntimeError("card handler failed")

        @d.on_error
        async def report(exc, event):
            return {"toast": {"type": "error", "content": "failed"}}

        result = await d.dispatch(_card_event())
        assert result == {"toast": {"type": "error", "content": "failed"}}

    async def test_raising_error_handler_swallowed(self, dispatcher):
        d = dispatcher
        ran = []

        @d.on("im.message.receive_v1")
        async def boom(event):
            raise RuntimeError("primary failed")

        @d.on_error
        async def bad_reporter(exc, event):
            raise RuntimeError("reporter also failed")

        @d.on("*")
        async def fallback(event):
            ran.append("fallback")

        result = await d.dispatch(_message_event())  # must not raise
        assert ran == ["fallback"]
        assert result is None
