import pytest

from tests.conftest import envelope, paginated_responder


@pytest.fixture
async def tables(client_factory, recorder):
    client = client_factory(recorder=recorder, responder=lambda r: envelope({}))
    yield client.bitable.tables
    await client.aclose()


class TestCreate:
    async def test_create_returns_table(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"table_id": "tblnew"}))
        resp = await client.bitable.tables.create("bascnxxx", {"name": "Tasks"})
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/bitable/v1/apps/bascnxxx/tables")
        assert body["table"] == {"name": "Tasks"}
        assert resp["table_id"] == "tblnew"
        await client.aclose()


class TestDelete:
    async def test_delete(self, tables, recorder):
        await tables.delete("bascnxxx", "tbl1")
        method, path, _, _ = recorder.last
        assert method == "DELETE" and path.endswith("/bitable/v1/apps/bascnxxx/tables/tbl1")


class TestList:
    async def test_paginates(self, client_factory, recorder):
        responder = paginated_responder([[{"table_id": "tbl1"}], [{"table_id": "tbl2"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.bitable.tables.list("bascnxxx")
        assert [i["table_id"] for i in items] == ["tbl1", "tbl2"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith("/bitable/v1/apps/bascnxxx/tables")
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_caps_page_size(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        await client.bitable.tables.list("bascnxxx", page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50
        await client.aclose()

    async def test_honors_max_items(self, client_factory, recorder):
        responder = paginated_responder([[{"table_id": "tbl1"}, {"table_id": "tbl2"}], [{"table_id": "tbl3"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.bitable.tables.list("bascnxxx", max_items=1)
        assert [i["table_id"] for i in items] == ["tbl1"]
        assert len(recorder) == 1
        await client.aclose()
