from tests.conftest import envelope, paginated_responder


class TestGet:
    async def test_get_returns_meeting(self, client_factory, recorder):
        client = client_factory(
            recorder=recorder,
            responder=lambda r: envelope({"meeting": {"id": "700", "topic": "周会"}}),
        )
        resp = await client.vc.meetings.get("700")
        method, path, params, _ = recorder.last
        assert method == "GET" and path.endswith("/vc/v1/meetings/700")
        assert params == {}  # flags omitted when unset
        assert resp["meeting"]["topic"] == "周会"
        await client.aclose()

    async def test_forwards_flags_and_user_id_type(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"meeting": {}}))
        await client.vc.meetings.get("700", with_participants=True, with_meeting_ability=True, user_id_type="open_id")
        params = recorder.last[2]
        assert params["with_participants"] == "true"
        assert params["with_meeting_ability"] == "true"
        assert params["user_id_type"] == "open_id"
        await client.aclose()


class TestListByNo:
    async def test_concatenates_pages(self, client_factory, recorder):
        responder = paginated_responder(
            [[{"id": "700", "meeting_no": "123456789"}], [{"id": "701"}]],
            data_key="meeting_briefs",
        )
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.vc.meetings.list_by_no("123456789", "1699999000", "1700002600")
        assert [i["id"] for i in items] == ["700", "701"]
        method, path, params, _ = recorder[0]
        assert method == "GET" and path.endswith("/vc/v1/meetings/list_by_no")
        assert params["meeting_no"] == "123456789"
        assert params["start_time"] == "1699999000"
        assert params["end_time"] == "1700002600"
        assert recorder[1][2]["page_token"] == "p2"  # token forwarded on page 2
        await client.aclose()

    async def test_honors_max_items(self, client_factory, recorder):
        responder = paginated_responder(
            [[{"id": "700"}, {"id": "701"}], [{"id": "702"}]],
            data_key="meeting_briefs",
        )
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.vc.meetings.list_by_no("123456789", "1", "2", max_items=1)
        assert [i["id"] for i in items] == ["700"]
        assert len(recorder) == 1
        await client.aclose()

    async def test_caps_page_size(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]], data_key="meeting_briefs"))
        await client.vc.meetings.list_by_no("123456789", "1", "2", page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50
        await client.aclose()
