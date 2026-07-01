import pytest

from tests.conftest import envelope, make_client, paginated_responder


@pytest.fixture
async def attendees(recorder):
    client = make_client(recorder=recorder, responder=lambda r: envelope({"attendees": []}))
    try:
        yield client.calendar.attendees
    finally:
        await client.aclose()


class TestAdd:
    async def test_posts_attendees(self, attendees, recorder):
        atts = [{"type": "user", "user_id": "ou_xxx"}]
        await attendees.add("cal1", "evt1", atts)
        method, path, _, body = recorder.last
        assert method == "POST"
        assert path.endswith("/calendar/v4/calendars/cal1/events/evt1/attendees")
        assert body["attendees"] == atts

    async def test_forwards_need_notification(self, attendees, recorder):
        await attendees.add("cal1", "evt1", [], need_notification=True)
        assert recorder.last[3]["need_notification"] is True

    async def test_omits_need_notification_when_unset(self, attendees, recorder):
        await attendees.add("cal1", "evt1", [])
        assert "need_notification" not in recorder.last[3]


class TestList:
    async def test_paginates(self, client_factory, recorder):
        responder = paginated_responder([[{"attendee_id": "a1"}], [{"attendee_id": "a2"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.calendar.attendees.list("cal1", "evt1")
        assert [i["attendee_id"] for i in items] == ["a1", "a2"]
        method, path, _, _ = recorder[0]
        assert method == "GET"
        assert path.endswith("/calendar/v4/calendars/cal1/events/evt1/attendees")
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_caps_page_size(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.calendar.attendees.list("cal1", "evt1", page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50
        await client.aclose()

    async def test_forwards_user_id_type(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.calendar.attendees.list("cal1", "evt1", user_id_type="union_id")
        assert recorder[0][2]["user_id_type"] == "union_id"
        await client.aclose()


class TestDelete:
    async def test_posts_attendee_ids(self, attendees, recorder):
        await attendees.delete("cal1", "evt1", ["a1", "a2"])
        method, path, _, body = recorder.last
        assert method == "POST"
        assert path.endswith("/calendar/v4/calendars/cal1/events/evt1/attendees/batch_delete")
        assert body["attendee_ids"] == ["a1", "a2"]
