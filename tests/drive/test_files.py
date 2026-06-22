import httpx
import pytest

from tests.conftest import envelope, make_client, token_handler


@pytest.fixture
async def files(client_factory, recorder):
    # One responder serving the union of keys these methods surface, so each test can
    # assert the value it cares about without a bespoke responder.
    data = {"metas": [], "file": {"token": "f2"}, "task_id": "t1", "token": "fld_2", "ticket": "tk1"}
    client = client_factory(recorder=recorder, responder=lambda r: envelope(data))
    yield client.drive.files
    await client.aclose()


def _files_responder(pages):
    """Serve drive list pages using the real wire keys: data.files / data.next_page_token.

    Mirrors ``paginated_responder`` but uses ``files`` and ``next_page_token`` so we exercise
    the namespace's key remapping. Non-final pages carry ``next_page_token`` ``tok2``, ``tok3`` ...
    """
    state = {"call": 0}

    def responder(request):
        idx = state["call"]
        state["call"] = idx + 1
        page = pages[idx] if idx < len(pages) else []
        has_more = idx < len(pages) - 1
        data = {"files": page, "has_more": has_more}
        if has_more:
            data["next_page_token"] = f"tok{idx + 2}"
        return envelope(data)

    return responder


def _bytes_client(content, seen):
    def handler(request):
        token = token_handler(request)
        if token is not None:
            return token
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, content=content)

    return make_client(handler=handler)


class TestList:
    async def test_concatenates_and_remaps_files_key(self, client_factory, recorder):
        # The list endpoint returns data.files / data.next_page_token (not items / page_token);
        # the namespace must remap those keys for the pagination helper, then carry the token forward.
        client = client_factory(recorder=recorder, responder=_files_responder([[{"token": "f1"}], [{"token": "f2"}]]))
        items = await client.drive.files.list()
        assert [i["token"] for i in items] == ["f1", "f2"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith("/drive/v1/files")
        assert recorder[1][2]["page_token"] == "tok2"
        await client.aclose()

    async def test_forwards_query_options(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=_files_responder([[]]))
        await client.drive.files.list(folder_token="fld_1", order_by="EditedTime", direction="DESC")
        params = recorder[0][2]
        assert params["folder_token"] == "fld_1"
        assert params["order_by"] == "EditedTime"
        assert params["direction"] == "DESC"

    async def test_omits_unset_folder_token(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=_files_responder([[]]))
        await client.drive.files.list()
        assert "folder_token" not in recorder[0][2]

    async def test_caps_page_size(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=_files_responder([[]]))
        await client.drive.files.list(page_size=9999)
        assert int(recorder[0][2]["page_size"]) <= 200

    async def test_honors_max_items(self, client_factory, recorder):
        client = client_factory(
            recorder=recorder,
            responder=_files_responder([[{"token": "f1"}, {"token": "f2"}], [{"token": "f3"}]]),
        )
        items = await client.drive.files.list(max_items=1)
        assert [i["token"] for i in items] == ["f1"]
        assert len(recorder) == 1


class TestGetMetas:
    async def test_posts_request_docs(self, files, recorder):
        docs = [{"doc_token": "doxcabc", "doc_type": "docx"}]
        resp = await files.get_metas(docs, with_url=True)
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/drive/v1/metas/batch_query")
        assert body["request_docs"] == docs
        assert body["with_url"] is True
        assert "metas" in resp

    async def test_defaults_and_forwards_options(self, files, recorder):
        await files.get_metas([{"doc_token": "d", "doc_type": "docx"}], user_id_type="open_id")
        method, path, params, body = recorder.last
        assert params["user_id_type"] == "open_id"
        assert body["with_url"] is False

    async def test_omits_unset_user_id_type(self, files, recorder):
        await files.get_metas([{"doc_token": "d", "doc_type": "docx"}])
        assert "user_id_type" not in recorder.last[2]


class TestCopy:
    async def test_posts_copy_request(self, files, recorder):
        extra = [{"key": "k", "value": "v"}]
        resp = await files.copy("f1", "Copy", doc_type="file", folder_token="fld_1", extra=extra)
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/drive/v1/files/f1/copy")
        assert body["name"] == "Copy"
        assert body["type"] == "file"  # doc_type maps to the wire field "type"
        assert body["folder_token"] == "fld_1"
        assert body["extra"] == extra
        assert "file" in resp


class TestMove:
    async def test_posts_move_request(self, files, recorder):
        resp = await files.move("f1", folder_token="fld_1", doc_type="file")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/drive/v1/files/f1/move")
        assert body["type"] == "file"  # doc_type maps to the wire field "type"
        assert body["folder_token"] == "fld_1"
        assert "task_id" in resp


class TestDelete:
    async def test_sends_doc_type_as_query_param(self, files, recorder):
        resp = await files.delete("f1", doc_type="file")
        method, path, params, _ = recorder.last
        assert method == "DELETE" and path.endswith("/drive/v1/files/f1")
        assert params["type"] == "file"
        assert "task_id" in resp


class TestCreateFolder:
    async def test_posts_name_and_folder_token(self, files, recorder):
        resp = await files.create_folder("New", "fld_1")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/drive/v1/files/create_folder")
        assert body["name"] == "New"
        assert body["folder_token"] == "fld_1"
        assert "token" in resp


class TestUpload:
    async def _upload_seen(self):
        seen = {}

        def handler(request):
            token = token_handler(request)
            if token is not None:
                return token
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["content_type"] = request.headers.get("content-type", "")
            seen["body"] = request.content
            return httpx.Response(200, json=envelope({"file_token": "boxcn1"}))

        return make_client(handler=handler), seen

    async def test_sends_multipart_fields(self):
        client, seen = await self._upload_seen()
        resp = await client.drive.files.upload("a.txt", "fld_1", b"hello")
        assert seen["method"] == "POST"
        assert seen["path"].endswith("/drive/v1/files/upload_all")
        assert seen["content_type"].startswith("multipart/form-data")
        body = seen["body"]
        # Form fields and the file bytes must be present in the multipart payload.
        assert b'name="file_name"' in body and b"a.txt" in body
        assert b'name="parent_type"' in body and b"explorer" in body
        assert b'name="parent_node"' in body and b"fld_1" in body
        assert b'name="size"' in body and b"5" in body
        assert b'name="file"' in body and b"hello" in body
        assert resp["file_token"] == "boxcn1"
        await client.aclose()

    @pytest.mark.parametrize(
        "kwargs, expected_size",
        [
            ({}, b"4"),  # size defaults to the byte length
            ({"size": 99}, b"99"),  # explicit size wins
        ],
    )
    async def test_size_field(self, kwargs, expected_size):
        client, seen = await self._upload_seen()
        await client.drive.files.upload("a.bin", "fld_1", b"abcd", **kwargs)
        assert b'name="size"' in seen["body"] and expected_size in seen["body"]
        await client.aclose()


class TestDownload:
    @pytest.mark.parametrize(
        "method_name, args, endpoint, content",
        [
            ("download", ("boxcn1",), "/drive/v1/files/boxcn1/download", b"FILE_BYTES"),
            ("download_export", ("boxcn1",), "/drive/v1/export_tasks/file/boxcn1/download", b"EXPORT_BYTES"),
        ],
    )
    async def test_returns_bytes_from_endpoint(self, method_name, args, endpoint, content):
        seen = {}
        client = _bytes_client(content, seen)
        result = await getattr(client.drive.files, method_name)(*args)
        assert seen["method"] == "GET"
        assert seen["path"].endswith(endpoint)
        assert result == content
        await client.aclose()


class TestExportTask:
    async def test_create_posts_flat_body(self, files, recorder):
        resp = await files.create_export_task("doxcabc", "pdf", doc_type="docx", sub_id="sub1")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/drive/v1/export_tasks")
        # Body is the flat ExportTask shape (not wrapped under a sub-key); doc_type maps to "type".
        assert body["file_extension"] == "pdf"
        assert body["token"] == "doxcabc"
        assert body["type"] == "docx"
        assert body["sub_id"] == "sub1"
        assert "ticket" in resp

    async def test_create_omits_unset_sub_id(self, files, recorder):
        await files.create_export_task("doxcabc", "pdf", doc_type="docx")
        assert "sub_id" not in recorder.last[3]

    async def test_get_fetches_ticket(self, client_factory, recorder):
        client = client_factory(
            recorder=recorder,
            responder=lambda r: envelope({"result": {"file_token": "boxcn1", "job_status": 0}}),
        )
        resp = await client.drive.files.get_export_task("tk1", token="doxcabc")
        method, path, params, _ = recorder.last
        assert method == "GET" and path.endswith("/drive/v1/export_tasks/tk1")
        assert params["token"] == "doxcabc"
        assert resp["result"]["file_token"] == "boxcn1"
        await client.aclose()
