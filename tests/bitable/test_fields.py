import pytest

from tests.conftest import paginated_responder


@pytest.fixture
async def fields(client_factory, recorder):
    def build(pages):
        return client_factory(recorder=recorder, responder=paginated_responder(pages))

    yield build


class TestList:
    async def test_concatenates_pages(self, fields, recorder):
        client = fields([[{"field_id": "fld1"}], [{"field_id": "fld2"}]])
        items = await client.bitable.fields.list("bascnxxx", "tbl1")
        assert [i["field_id"] for i in items] == ["fld1", "fld2"]
        method, path, _, _ = recorder[0]
        assert method == "GET"
        assert path.endswith("/bitable/v1/apps/bascnxxx/tables/tbl1/fields")
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_caps_page_size(self, fields, recorder):
        client = fields([[]])
        await client.bitable.fields.list("bascnxxx", "tbl1", page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50
        await client.aclose()

    async def test_respects_max_items(self, fields, recorder):
        client = fields([[{"field_id": "fld1"}], [{"field_id": "fld2"}]])
        items = await client.bitable.fields.list("bascnxxx", "tbl1", max_items=1)
        assert [i["field_id"] for i in items] == ["fld1"]
        await client.aclose()
