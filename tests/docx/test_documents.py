import pytest

from tests.conftest import envelope, paginated_responder


def document_responder(request):
    return envelope({"document": {"document_id": "doxcabc", "revision_id": 1, "title": "My Doc"}})


@pytest.fixture
async def docx(client_factory, recorder):
    """A docx namespace returning a stub document; auto-closed after the test."""
    client = client_factory(recorder=recorder, responder=document_responder)
    try:
        yield client.docx
    finally:
        await client.aclose()


class TestCreate:
    async def test_create_returns_document(self, docx, recorder):
        resp = await docx.create(title="My Doc")
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/docx/v1/documents")
        assert body["title"] == "My Doc"
        assert resp["document"]["document_id"] == "doxcabc"

    async def test_forwards_folder_token(self, docx, recorder):
        await docx.create(title="My Doc", folder_token="fld_1")
        assert recorder.last[3]["folder_token"] == "fld_1"

    async def test_omits_unset_folder_token(self, docx, recorder):
        await docx.create(title="My Doc")
        assert "folder_token" not in recorder.last[3]


class TestGet:
    async def test_get_returns_document(self, docx, recorder):
        resp = await docx.get("doxcabc")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/docx/v1/documents/doxcabc")
        assert resp["document"]["title"] == "My Doc"


class TestRawContent:
    @pytest.fixture
    async def get_raw_content(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"content": "Hello world\n"}))
        try:
            yield client.docx.get_raw_content
        finally:
            await client.aclose()

    async def test_returns_content(self, get_raw_content, recorder):
        content = await get_raw_content("doxcabc")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/docx/v1/documents/doxcabc/raw_content")
        assert content == "Hello world\n"

    async def test_forwards_lang(self, get_raw_content, recorder):
        await get_raw_content("doxcabc", lang=2)
        assert recorder.last[2]["lang"] == "2"

    async def test_omits_unset_lang(self, get_raw_content, recorder):
        await get_raw_content("doxcabc")
        assert "lang" not in recorder.last[2]


class TestListBlocks:
    async def test_concatenates_pages(self, client_factory, recorder):
        responder = paginated_responder([[{"block_id": "blk1"}], [{"block_id": "blk2"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.docx.list_blocks("doxcabc")
        assert [i["block_id"] for i in items] == ["blk1", "blk2"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith("/docx/v1/documents/doxcabc/blocks")
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    @pytest.mark.parametrize(
        "page_size, expected",
        [(None, 500), (9999, 500)],
        ids=["default", "capped"],
    )
    async def test_page_size(self, client_factory, recorder, page_size, expected):
        client = client_factory(recorder=recorder, responder=paginated_responder([[]]))
        kwargs = {} if page_size is None else {"page_size": page_size}
        await client.docx.list_blocks("doxcabc", **kwargs)
        assert int(recorder[0][2]["page_size"]) == expected
        await client.aclose()

    async def test_honors_max_items(self, client_factory, recorder):
        responder = paginated_responder([[{"block_id": "blk1"}, {"block_id": "blk2"}], [{"block_id": "blk3"}]])
        client = client_factory(recorder=recorder, responder=responder)
        items = await client.docx.list_blocks("doxcabc", max_items=1)
        assert [i["block_id"] for i in items] == ["blk1"]
        assert len(recorder) == 1
        await client.aclose()


class TestGetBlock:
    async def test_get_block_returns_block(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"block_id": "blk2", "block_type": 2}))
        resp = await client.docx.get_block("doxcabc", "blk2")
        method, path, _, _ = recorder.last
        assert method == "GET" and path.endswith("/docx/v1/documents/doxcabc/blocks/blk2")
        assert resp["block_id"] == "blk2"
        await client.aclose()


class TestAppendBlocks:
    @pytest.fixture
    async def append(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"children": []}))
        try:
            yield client.docx.append_blocks
        finally:
            await client.aclose()

    async def test_targets_document_root_by_default(self, append, recorder):
        children = [{"block_type": 2}]
        await append("doxcabc", children)
        method, path, _, body = recorder.last
        assert method == "POST"
        # With no block_id, the document root (document_id) is the parent.
        assert path.endswith("/docx/v1/documents/doxcabc/blocks/doxcabc/children")
        assert body["children"] == children

    async def test_targets_given_parent(self, append, recorder):
        await append("doxcabc", [{"block_type": 2}], block_id="blkParent")
        assert recorder.last[1].endswith("/docx/v1/documents/doxcabc/blocks/blkParent/children")

    async def test_forwards_index(self, append, recorder):
        await append("doxcabc", [{"block_type": 2}], index=0)
        assert recorder.last[3]["index"] == 0

    async def test_omits_unset_index(self, append, recorder):
        await append("doxcabc", [{"block_type": 2}])
        assert "index" not in recorder.last[3]


class TestPatchBlock:
    async def test_patch_block_sends_update(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"block": {"block_id": "blk2"}}))
        update = {"update_text_elements": {"elements": [{"text_run": {"content": "new"}}]}}
        resp = await client.docx.patch_block("doxcabc", "blk2", update)
        method, path, _, body = recorder.last
        assert method == "PATCH" and path.endswith("/docx/v1/documents/doxcabc/blocks/blk2")
        assert body == update
        assert resp["block"]["block_id"] == "blk2"
        await client.aclose()


class TestBatchUpdateBlocks:
    async def test_batch_update_sends_requests(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=lambda r: envelope({"blocks": []}))
        reqs = [{"block_id": "blk2", "update_text_elements": {"elements": []}}]
        await client.docx.batch_update_blocks("doxcabc", reqs)
        method, path, _, body = recorder.last
        assert method == "PATCH" and path.endswith("/docx/v1/documents/doxcabc/blocks/batch_update")
        assert body["requests"] == reqs
        await client.aclose()
