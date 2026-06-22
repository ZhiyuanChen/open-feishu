from tests.conftest import envelope, paginated_responder


class TestCRUD:
    async def test_create_returns_calendar(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"calendar": {"calendar_id": "cal1"}}))
        resp = await client.calendar.calendars.create({"summary": "团队日历"})
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/calendar/v4/calendars")
        assert body["summary"] == "团队日历"
        assert resp["calendar"]["calendar_id"] == "cal1"
        await client.aclose()

    async def test_get_returns_calendar(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"calendar": {"calendar_id": "cal1"}}))
        resp = await client.calendar.calendars.get("cal1")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/calendar/v4/calendars/cal1")
        assert resp["calendar"]["calendar_id"] == "cal1"
        await client.aclose()

    async def test_update_patches_body(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"calendar": {"calendar_id": "cal1"}}))
        await client.calendar.calendars.update("cal1", {"summary": "新名称"})
        method, path, _, body = recorder.last
        assert method == "PATCH" and path.endswith("/calendar/v4/calendars/cal1")
        assert body["summary"] == "新名称"
        await client.aclose()

    async def test_delete(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({}))
        await client.calendar.calendars.delete("cal1")
        method, path, _, _ = recorder.last
        assert method == "DELETE" and path.endswith("/calendar/v4/calendars/cal1")
        await client.aclose()


class TestList:
    async def test_paginates(self, client_factory, recorder):
        responder = paginated_responder(
            [[{"calendar_id": "cal1"}], [{"calendar_id": "cal2"}]], data_key="calendar_list"
        )
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.calendar.calendars.list()
        assert [i["calendar_id"] for i in items] == ["cal1", "cal2"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith("/calendar/v4/calendars")
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_caps_page_size(self, client_factory, recorder):
        responder = paginated_responder([[]], data_key="calendar_list")
        client = client_factory(recorder=recorder, responder=responder)
        await client.calendar.calendars.list(page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50
        await client.aclose()

    async def test_honors_max_items(self, client_factory, recorder):
        responder = paginated_responder(
            [[{"calendar_id": "cal1"}, {"calendar_id": "cal2"}], [{"calendar_id": "cal3"}]],
            data_key="calendar_list",
        )
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.calendar.calendars.list(max_items=1)
        assert [i["calendar_id"] for i in items] == ["cal1"]
        assert len(recorder) == 1
        await client.aclose()


class TestPrimary:
    async def test_posts_to_primary(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"calendars": []}))
        resp = await client.calendar.calendars.primary()
        method, path, params, _ = recorder.last
        assert method == "POST" and path.endswith("/calendar/v4/calendars/primary")
        assert "user_id_type" not in params
        assert resp["calendars"] == []
        await client.aclose()

    async def test_forwards_user_id_type(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"calendars": []}))
        await client.calendar.calendars.primary(user_id_type="open_id")
        assert recorder.last[2]["user_id_type"] == "open_id"
        await client.aclose()
