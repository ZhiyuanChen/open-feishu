import pytest

from tests.conftest import envelope, paginated_responder


@pytest.fixture
async def events(client_factory, recorder):
    client = client_factory(recorder=recorder, responder=lambda r: envelope({"event": {"event_id": "evt1"}}))
    yield client.calendar.events
    await client.aclose()


class TestCreate:
    async def test_create_returns_event(self, events, recorder):
        resp = await events.create("cal1", {"summary": "周会"})
        method, path, params, body = recorder.last
        assert method == "POST" and path.endswith("/calendar/v4/calendars/cal1/events")
        assert body["summary"] == "周会"
        assert "idempotency_key" not in params
        assert resp["event"]["event_id"] == "evt1"

    async def test_forwards_idempotency_key(self, events, recorder):
        await events.create("cal1", {"summary": "周会"}, idempotency_key="idem1")
        assert recorder.last[2]["idempotency_key"] == "idem1"


class TestGet:
    async def test_get_returns_event(self, events, recorder):
        resp = await events.get("cal1", "evt1")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/calendar/v4/calendars/cal1/events/evt1")
        assert resp["event"]["event_id"] == "evt1"


class TestUpdate:
    async def test_update_patches_body(self, events, recorder):
        resp = await events.update("cal1", "evt1", {"summary": "改期"})
        method, path, _, body = recorder.last
        assert method == "PATCH" and path.endswith("/calendar/v4/calendars/cal1/events/evt1")
        assert body["summary"] == "改期"
        assert resp["event"]["event_id"] == "evt1"


class TestDelete:
    async def test_delete_deletes(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({}))
        await client.calendar.events.delete("cal1", "evt1")
        method, path, _, _ = recorder.last
        assert method == "DELETE" and path.endswith("/calendar/v4/calendars/cal1/events/evt1")
        await client.aclose()


class TestList:
    async def test_paginates(self, client_factory, recorder):
        responder = paginated_responder([[{"event_id": "evt1"}], [{"event_id": "evt2"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.calendar.events.list("cal1")
        assert [i["event_id"] for i in items] == ["evt1", "evt2"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith("/calendar/v4/calendars/cal1/events")
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_caps_page_size(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.calendar.events.list("cal1", page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50
        await client.aclose()

    async def test_honors_max_items(self, client_factory, recorder):
        responder = paginated_responder([[{"event_id": "evt1"}, {"event_id": "evt2"}], [{"event_id": "evt3"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.calendar.events.list("cal1", max_items=1)
        assert [i["event_id"] for i in items] == ["evt1"]
        assert len(recorder) == 1
        await client.aclose()

    async def test_omits_unset_params(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.calendar.events.list("cal1")
        assert "start_time" not in recorder[0][2]
        assert "end_time" not in recorder[0][2]
        await client.aclose()

    async def test_forwards_time_range(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.calendar.events.list("cal1", start_time="100", end_time="200")
        params = recorder[0][2]
        assert params["start_time"] == "100" and params["end_time"] == "200"
        await client.aclose()
