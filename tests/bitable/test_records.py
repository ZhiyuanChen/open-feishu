import pytest

from tests.conftest import envelope, paginated_responder


def record_responder(request):
    return envelope({"record": {"record_id": "recxxx", "fields": {"Title": "hi"}}})


@pytest.fixture
async def records(client_factory, recorder):
    client = client_factory(recorder=recorder, responder=record_responder)
    try:
        yield client.bitable.records
    finally:
        await client.aclose()


PREFIX = "/bitable/v1/apps/bascnxxx/tables/tbl1/records"


class TestWriteAndRead:
    """Single-record + batch endpoints: verb, path, and the body/return the caller cares about."""

    async def test_create(self, records, recorder):
        resp = await records.create("bascnxxx", "tbl1", {"Title": "hi"})
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith(PREFIX)
        assert body["fields"] == {"Title": "hi"}
        assert resp["record"]["record_id"] == "recxxx"

    async def test_update(self, records, recorder):
        resp = await records.update("bascnxxx", "tbl1", "recxxx", {"Title": "new"})
        method, path, _, body = recorder.last
        assert method == "PUT" and path.endswith(f"{PREFIX}/recxxx")
        assert body["fields"] == {"Title": "new"}
        assert resp["record"]["record_id"] == "recxxx"

    async def test_get(self, records, recorder):
        resp = await records.get("bascnxxx", "tbl1", "recxxx")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith(f"{PREFIX}/recxxx")
        assert resp["record"]["record_id"] == "recxxx"

    async def test_delete(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"deleted": True}))
        resp = await client.bitable.records.delete("bascnxxx", "tbl1", "recxxx")
        method, path, _, _ = recorder.last
        assert method == "DELETE" and path.endswith(f"{PREFIX}/recxxx")
        assert resp["deleted"] is True
        await client.aclose()

    @pytest.mark.parametrize(
        "op, args, suffix",
        [
            ("batch_create", ([{"fields": {"Title": "a"}}, {"fields": {"Title": "b"}}],), "batch_create"),
            ("batch_update", ([{"record_id": "rec1", "fields": {"Title": "a"}}],), "batch_update"),
            ("batch_delete", (["rec1", "rec2"],), "batch_delete"),
        ],
    )
    async def test_batch_ops(self, client_factory, recorder, op, args, suffix):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"records": []}))
        await getattr(client.bitable.records, op)("bascnxxx", "tbl1", *args)
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith(f"{PREFIX}/{suffix}")
        assert body["records"] == args[0]
        await client.aclose()


class TestList:
    async def test_paginates(self, client_factory, recorder):
        responder = paginated_responder([[{"record_id": "rec1"}], [{"record_id": "rec2"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.bitable.records.list("bascnxxx", "tbl1")
        assert [i["record_id"] for i in items] == ["rec1", "rec2"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith(PREFIX)
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_caps_page_size(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.bitable.records.list("bascnxxx", "tbl1", page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50
        await client.aclose()

    async def test_honors_max_items(self, client_factory, recorder):
        responder = paginated_responder([[{"record_id": "rec1"}, {"record_id": "rec2"}], [{"record_id": "rec3"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.bitable.records.list("bascnxxx", "tbl1", max_items=1)
        assert [i["record_id"] for i in items] == ["rec1"]
        assert len(recorder) == 1
        await client.aclose()

    async def test_forwards_optional_params(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.bitable.records.list("bascnxxx", "tbl1", view_id="vew1", filter="f", sort="s", field_names="Title")
        params = recorder[0][2]
        assert params["view_id"] == "vew1"
        assert params["filter"] == "f"
        assert params["sort"] == "s"
        assert params["field_names"] == "Title"
        await client.aclose()

    async def test_omits_unset_params(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.bitable.records.list("bascnxxx", "tbl1")
        assert "view_id" not in recorder[0][2]
        await client.aclose()


class TestSearch:
    async def test_posts_body_and_paginates(self, client_factory, recorder):
        responder = paginated_responder([[{"record_id": "rec1"}], [{"record_id": "rec2"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.bitable.records.search("bascnxxx", "tbl1", {"field_names": ["Title"]})
        assert [i["record_id"] for i in items] == ["rec1", "rec2"]
        method, path, _, body = recorder[0]
        assert method == "POST" and path.endswith(f"{PREFIX}/search")
        assert body["field_names"] == ["Title"]
        # page_token rides in the query params, not the body.
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_caps_page_size(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.bitable.records.search("bascnxxx", "tbl1", {}, page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50
        await client.aclose()
