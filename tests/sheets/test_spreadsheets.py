import pytest

from tests.conftest import envelope


def spreadsheet_responder(request):
    return envelope({"spreadsheet": {"spreadsheet_token": "shtcn_xxx", "title": "My Sheet"}})


@pytest.fixture
async def sheets(client_factory, recorder):
    client = client_factory(recorder=recorder, responder=spreadsheet_responder)
    try:
        yield client.sheets
    finally:
        await client.aclose()


class TestCreate:
    async def test_create_returns_spreadsheet(self, sheets, recorder):
        resp = await sheets.create("My Sheet")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/sheets/v3/spreadsheets")
        assert body["title"] == "My Sheet"
        assert resp["spreadsheet"]["spreadsheet_token"] == "shtcn_xxx"

    async def test_forwards_folder_token(self, sheets, recorder):
        await sheets.create("My Sheet", folder_token="fldcn123")
        assert recorder.last[3]["folder_token"] == "fldcn123"

    async def test_omits_unset_folder_token(self, sheets, recorder):
        await sheets.create("My Sheet")
        assert "folder_token" not in recorder.last[3]


class TestGet:
    async def test_get_returns_spreadsheet(self, sheets, recorder):
        resp = await sheets.get("shtcn_xxx")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/sheets/v3/spreadsheets/shtcn_xxx")
        assert resp["spreadsheet"]["title"] == "My Sheet"


class TestRename:
    async def test_rename_patches_title(self, sheets, recorder):
        await sheets.rename("shtcn_xxx", "New Title")
        method, path, _, body = recorder.last
        assert method == "PATCH" and path.endswith("/sheets/v3/spreadsheets/shtcn_xxx")
        assert body["title"] == "New Title"


class TestListSheets:
    async def test_returns_sheets(self, client_factory, recorder):
        sheets_data = [{"sheet_id": "0b**12", "title": "Sheet1"}, {"sheet_id": "0b**34", "title": "Sheet2"}]
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"sheets": sheets_data}))
        resp = await client.sheets.list_sheets("shtcn_xxx")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/sheets/v3/spreadsheets/shtcn_xxx/sheets/query")
        assert [s["sheet_id"] for s in resp] == ["0b**12", "0b**34"]
        await client.aclose()

    async def test_empty_when_absent(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({}))
        resp = await client.sheets.list_sheets("shtcn_xxx")
        assert resp == []
        await client.aclose()


class TestReadRange:
    async def test_reads_v2_values(self, client_factory, recorder):
        data = {"valueRange": {"range": "Q7PlXT!A1:B2", "values": [["a", "b"]]}}
        client = client_factory(recorder=recorder, responder=lambda r: envelope(data))
        resp = await client.sheets.read_range("shtcn_xxx", "Q7PlXT!A1:B2")
        method, path, _, _ = recorder.last
        assert method == "GET"
        assert path.endswith("/sheets/v2/spreadsheets/shtcn_xxx/values/Q7PlXT!A1:B2")
        assert resp["valueRange"]["values"] == [["a", "b"]]
        await client.aclose()

    async def test_forwards_render_option(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({}))
        await client.sheets.read_range("shtcn_xxx", "Q7PlXT!A1:B2", value_render_option="ToString")
        assert recorder.last[2]["valueRenderOption"] == "ToString"
        await client.aclose()

    async def test_omits_unset_render_option(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({}))
        await client.sheets.read_range("shtcn_xxx", "Q7PlXT!A1:B2")
        assert "valueRenderOption" not in recorder.last[2]
        await client.aclose()

    async def test_encodes_path_injection_in_range(self, client_factory):
        # A `/` in the caller-supplied range is percent-encoded on the wire so it cannot
        # escape the values/<range> segment, while the `!`/`:` range delimiters are kept.
        captured = {}

        def responder(request):
            captured["raw_path"] = request.url.raw_path.decode("ascii")
            return envelope({"valueRange": {}})

        client = client_factory(responder=responder)
        await client.sheets.read_range("shtcn_xxx", "Evil/Sheet!A1:B2")
        assert "/values/Evil%2FSheet!A1:B2" in captured["raw_path"]
        await client.aclose()


class TestWriteRange:
    async def test_puts_value_range(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"updatedCells": 4}))
        resp = await client.sheets.write_range("shtcn_xxx", "Q7PlXT!A1:B2", [["a", "b"], ["c", "d"]])
        method, path, _, body = recorder.last
        assert method == "PUT" and path.endswith("/sheets/v2/spreadsheets/shtcn_xxx/values")
        assert body["valueRange"] == {"range": "Q7PlXT!A1:B2", "values": [["a", "b"], ["c", "d"]]}
        assert resp["updatedCells"] == 4
        await client.aclose()


class TestAppendRows:
    async def test_posts_value_range(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"tableRange": "Q7PlXT!A1:B3"}))
        resp = await client.sheets.append_rows("shtcn_xxx", "Q7PlXT!A1:B2", [["e", "f"]])
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/sheets/v2/spreadsheets/shtcn_xxx/values_append")
        assert body["valueRange"] == {"range": "Q7PlXT!A1:B2", "values": [["e", "f"]]}
        assert resp["tableRange"] == "Q7PlXT!A1:B3"
        await client.aclose()
