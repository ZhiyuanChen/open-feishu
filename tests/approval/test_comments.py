import pytest

from tests.conftest import envelope, paginated_responder


@pytest.fixture
async def comments(client_factory, recorder):
    client = client_factory(recorder=recorder, responder=lambda r: envelope({"comment_id": "C1"}))
    yield client.approval.comments
    await client.aclose()


class TestCreate:
    async def test_create_returns_comment(self, comments, recorder):
        resp = await comments.create("INST1", {"content": "请补充材料"})
        method, path, _, body = recorder.last
        assert method == "POST" and path.endswith("/approval/v4/instances/INST1/comments")
        assert body["content"] == "请补充材料"
        assert resp["comment_id"] == "C1"

    async def test_forwards_user_id(self, comments, recorder):
        await comments.create("INST1", {"content": "hi"}, user_id="u1")
        assert recorder.last[2]["user_id"] == "u1"


class TestList:
    @pytest.fixture
    def paged(self, client_factory, recorder):
        def _make(pages, **kw):
            responder = paginated_responder(pages, data_key="comments")
            return client_factory(recorder=recorder, responder=responder)

        return _make

    async def test_concatenates_pages(self, paged, recorder):
        client = paged([[{"id": "C1"}], [{"id": "C2"}]])
        comments = await client.approval.comments.list("INST1")
        assert [c["id"] for c in comments] == ["C1", "C2"]
        method, path, _, _ = recorder[0]
        assert method == "GET" and path.endswith("/approval/v4/instances/INST1/comments")
        assert recorder[1][2]["page_token"] == "p2"
        await client.aclose()

    async def test_caps_page_size(self, paged, recorder):
        client = paged([[]])
        await client.approval.comments.list("INST1", page_size=999)
        assert int(recorder[0][2]["page_size"]) <= 50
        await client.aclose()

    async def test_forwards_user_id(self, paged, recorder):
        client = paged([[]])
        await client.approval.comments.list("INST1", user_id="u1")
        assert recorder[0][2]["user_id"] == "u1"
        await client.aclose()

    async def test_omits_user_id_when_unset(self, paged, recorder):
        client = paged([[]])
        await client.approval.comments.list("INST1")
        assert "user_id" not in recorder[0][2]
        await client.aclose()
